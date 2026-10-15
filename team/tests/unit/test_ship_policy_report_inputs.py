"""
test_ship_policy_report_inputs.py
=================================
The ship-policy matrix's report inputs, read through the one TESTER-report
parser: read_ship_policy_finding_dimensions and read_ship_policy_report_values
(team/scripts/lib/cm/release_ship_policy_report.sh), which run
pgai_agent_kanban.cm.ship_policy_report_inputs over parse_report.

Coverage:
  - Corpus parity. For every report in tests/fixtures/tester_reports/corpus/
    the two bash readers print exactly the stdout recorded in
    tests/fixtures/tester_reports/ship_policy_inputs.json, which holds what
    the readers printed before they read reports through the parser. The
    same expected values are also what each report's parser golden file
    (golden/<report>.json) projects to, so the recorded outputs, the goldens
    and the production readers cannot drift apart. A report listed in
    differential.md follows its golden; for every such report today the
    golden projection and the recorded output are the same, because every
    differential.md row is a Regression annotation or a priority Filed As,
    and neither changes a value these readers print.
  - The matrix decision for a high-risk, regression, destructive report is
    the one the release tag before the migration reached (see
    test_high_risk_regression_destructive_report_reaches_the_recorded_decision
    for how that decision was obtained).
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from pgai_agent_kanban.cm.ship_policy_report_inputs import finding_dimensions, report_values
# Imported as a module: a bare TesterReport name here would be collected by
# pytest as a test class.
from pgai_agent_kanban.reports import tester_report
from tests.unit.shell_harness import run_bash

# Path to the library under test, relative to team/ (run_bash's cwd).
_LIB = "scripts/lib/cm/release_ship_policy.sh"

# This file lives at team/tests/unit/, so two parents up is team/.
_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_FIXTURES = _TEAM_DIR / "tests" / "fixtures" / "tester_reports"
_CORPUS = _FIXTURES / "corpus"
_GOLDEN = _FIXTURES / "golden"
_DIFFERENTIAL = _FIXTURES / "differential.md"
_EXPECTED = json.loads((_FIXTURES / "ship_policy_inputs.json").read_text(encoding="utf-8"))["reports"]
_CORPUS_FILES = sorted(_CORPUS.glob("*.md"))

# differential.md names each report with a disagreement in a "### <report>.md"
# heading above its table.
_DIFFERENTIAL_REPORT_RE = re.compile(r"^\s*#{3}\s+`?(?P<name>[^`\s]+\.md)`?\s*$", re.MULTILINE)
_DIFFERENTIAL_REPORTS = frozenset(
    m.group("name") for m in _DIFFERENTIAL_REPORT_RE.finditer(_DIFFERENTIAL.read_text(encoding="utf-8"))
)

# Separates the two readers' stdout in one harness run.
_SEPARATOR = "----- values -----"


def _diagnostic(result) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _read_both(tmp_path: pathlib.Path, report: pathlib.Path):
    """Run both production readers on *report* in one bash process; return
    ``(dims stdout line, values stdout lines, harness result)``."""
    result = run_bash(
        tmp_path,
        f"source {_LIB} && "
        f"read_ship_policy_finding_dimensions '{report}' 2>/dev/null && "
        f"echo '{_SEPARATOR}' && "
        f"read_ship_policy_report_values '{report}' 2>/dev/null",
    )
    assert result.returncode == 0, _diagnostic(result)
    dims_part, sep, values_part = result.stdout.partition(_SEPARATOR + "\n")
    assert sep, _diagnostic(result)
    return dims_part.rstrip("\n"), values_part.splitlines(), result


def _finding_from_golden(data: dict) -> tester_report.Finding:
    fields = dict(data)
    fields["sub_findings"] = [_finding_from_golden(sub) for sub in data["sub_findings"]]
    return tester_report.Finding(**fields)


def _report_from_golden(name: str) -> tester_report.TesterReport:
    data = json.loads((_GOLDEN / (pathlib.Path(name).stem + ".json")).read_text(encoding="utf-8"))
    return tester_report.TesterReport(
        recommendation=data["recommendation"],
        systemic_risk=data["systemic_risk"],
        summary=data["summary"],
        findings=[_finding_from_golden(f) for f in data["findings"]],
        raw=data["raw"],
    )


def test_every_corpus_report_has_a_recorded_output():
    """The recorded outputs cover the corpus exactly, so a report added to
    the corpus without its expected reader output is caught here."""
    assert sorted(_EXPECTED) == [path.name for path in _CORPUS_FILES]


@pytest.mark.parametrize("report", _CORPUS_FILES, ids=lambda path: path.stem)
def test_readers_print_the_recorded_output_for_each_corpus_report(tmp_path, report):
    """Both bash readers print the recorded stdout for this report, and the
    report's parser golden projects to the same values."""
    expected = _EXPECTED[report.name]
    golden = _report_from_golden(report.name)
    golden_dims = " ".join(finding_dimensions(golden))
    golden_values = [f"{key}={value}" for key, value in report_values(golden).items()]
    origin = "its golden (listed in differential.md)" if report.name in _DIFFERENTIAL_REPORTS else "the pre-parser readers"

    assert golden_dims == expected["dims"], f"{report.name}: golden projection differs from {origin}"
    assert golden_values == expected["values"], f"{report.name}: golden projection differs from {origin}"

    dims, values, result = _read_both(tmp_path, report)
    assert dims == expected["dims"], _diagnostic(result)
    assert values == expected["values"], _diagnostic(result)


# ---------------------------------------------------------------------------
# The matrix decision for a high-risk, regression, destructive report.
# ---------------------------------------------------------------------------

_HIGH_RISK_REGRESSION_DESTRUCTIVE_REPORT = """\
# Verification Report: rc/v0.0.0

## Recommendation

SHIP-WITH-SERIOUS-CONCERNS

## Systemic Risk

high

## Findings

### Finding 1: a low-risk finding that predates the RC

**Category:** bug
**Severity:** low
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low
**Regression:** no
**Integrity:** none
**Filed As:** BUG-0001

### Finding 2: the release deletes operator data on upgrade

**Category:** bug
**Severity:** critical
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** high
**Regression:** yes
**Integrity:** destructive
**Filed As:** BUG-0002

## Lint Findings (non-blocking)

None.
"""

# What the release tag ai_v2.32.10 printed for the report above. Obtained by
# exporting that tag's team/ tree (git archive ai_v2.32.10 team), sourcing
# its scripts/lib/cm/release_ship_policy.sh, reading the report with its
# read_ship_policy_report_values and read_ship_policy_finding_dimensions,
# and passing the values to its cm_ship_policy exactly as
# _decide_like_step_4d below does (tester state done, task found yes):
#   values: recommendation=ship-with-serious-concerns systemic_risk=high
#           has_large_fix_effort=no; dims: yes destructive
#   decision: HALT
_RECORDED_VALUES = {
    "recommendation": "ship-with-serious-concerns",
    "systemic_risk": "high",
    "has_large_fix_effort": "no",
}
_RECORDED_DIMS = "yes destructive"
_RECORDED_DECISION = "HALT"


def _decide_like_step_4d(tmp_path: pathlib.Path, report: pathlib.Path):
    """Read *report* with both readers and feed cm_ship_policy the way
    cm_release_apply_ship_policy's Step 4d does; return the harness result,
    whose stdout is "values ... / dims ... / decision ..."."""
    return run_bash(
        tmp_path,
        f"source {_LIB} && "
        "declare -A v && while IFS= read -r line; do v[\"${line%%=*}\"]=\"${line#*=}\"; done "
        f"< <(read_ship_policy_report_values '{report}' 2>/dev/null) && "
        f"read -r reg integ < <(read_ship_policy_finding_dimensions '{report}' 2>/dev/null) && "
        "echo \"recommendation=${v[recommendation]}\" && "
        "echo \"systemic_risk=${v[systemic_risk]}\" && "
        "echo \"has_large_fix_effort=${v[has_large_fix_effort]}\" && "
        "echo \"dims=$reg $integ\" && "
        "echo \"decision=$(cm_ship_policy done \"${v[systemic_risk]}\" \"${v[recommendation]}\" "
        "\"${v[has_large_fix_effort]}\" yes \"$reg\" \"$integ\")\"",
    )


def test_high_risk_regression_destructive_report_reaches_the_recorded_decision(tmp_path):
    """A report whose high-risk finding is a regression with destructive
    integrity damage (beside an unrelated low-risk finding) reads the same
    values, and reaches the same cm_ship_policy decision, through the parser
    as the recorded pre-parser release reached: HALT."""
    report = tmp_path / "report.md"
    report.write_text(_HIGH_RISK_REGRESSION_DESTRUCTIVE_REPORT, encoding="utf-8")
    result = _decide_like_step_4d(tmp_path, report)
    assert result.returncode == 0, _diagnostic(result)
    got = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    for key, value in _RECORDED_VALUES.items():
        assert got.get(key) == value, _diagnostic(result)
    assert got.get("dims") == _RECORDED_DIMS, _diagnostic(result)
    assert got.get("decision") == _RECORDED_DECISION, _diagnostic(result)


# ---------------------------------------------------------------------------
# The module's own edges, on its import surface.
# ---------------------------------------------------------------------------


def _report(recommendation_raw=None, systemic_risk_raw=None, findings=()):
    text = "# Verification Report\n\n"
    if recommendation_raw is not None:
        text += f"## Recommendation\n\n{recommendation_raw}\n\n"
    if systemic_risk_raw is not None:
        text += f"## Systemic Risk\n\n{systemic_risk_raw}\n\n"
    text += "## Findings\n\n" + "".join(findings)
    return tester_report.parse_report_text(text)


@pytest.mark.parametrize(
    "systemic_risk_raw, expected",
    [
        ("none", "none"),
        ("**None**", "none"),
        ("None — nothing in this RC carries risk", "none"),
        ("Systemic Risk: none", "none"),
        ("nonesuch", "unparseable"),
        ("hgih", "unparseable"),
        ("High — one dashboard finding", "high"),
    ],
)
def test_systemic_risk_none_is_a_matrix_token_and_anything_else_unrecognised_is_unparseable(
    systemic_risk_raw, expected
):
    """"none" is a systemic-risk token the matrix has a row for (not-high),
    so a value line that leads with it reads none, never unparseable."""
    values = report_values(_report("PASS", systemic_risk_raw))
    assert values["systemic_risk"] == expected
    assert values["systemic_risk_raw"] == systemic_risk_raw


@pytest.mark.parametrize(
    "recommendation_raw, expected",
    [("none", "none"), ("`none`", "none"), ("None yet -- verification incomplete", "unparseable")],
)
def test_recommendation_reads_none_only_for_a_bare_none(recommendation_raw, expected):
    """For Recommendation "none" is not a token, so only a value line that is
    exactly "none" reads none; anything more is unparseable."""
    assert report_values(_report(recommendation_raw, "low"))["recommendation"] == expected


def test_nested_finding_is_consulted_and_outer_finding_keeps_its_own_fields():
    """A high-risk finding nested inside another counts like any other, and
    the outer finding's own fields -- written after the nested one -- are
    still read: the outer large fix effort sets the flag."""
    report = _report(
        "SHIP-WITH-SERIOUS-CONCERNS",
        "high",
        [
            "### Finding 1: outer\n\n",
            "#### Finding 2: nested high-risk regression\n\n",
            "**Systemic Risk:** high\n**Regression:** yes\n**Integrity:** none\n\n",
            "### Step 3: unrelated\n\n",
        ],
    )
    assert finding_dimensions(report) == ("yes", "none")
    outer_after_nested = _report(
        "PASS",
        "low",
        [
            "### Finding 1: outer\n\n",
            "#### Finding 2: nested\n\n**Fix Effort:** small\n\n",
            "#### Notes\n\n**Fix Effort:** large\n\n",
        ],
    )
    assert report_values(outer_after_nested)["has_large_fix_effort"] == "yes"
