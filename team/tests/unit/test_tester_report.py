"""
test_tester_report.py
=====================
Behaviour of the one TESTER-report parser,
pgai_agent_kanban.reports.tester_report, its CLI and its bash wrapper
team/scripts/lib/tester_report.sh.

Covered, in the order of the module docstring's rules:
  1. What starts a finding: heading levels 2-6, the whole word "Finding",
     the no-space form, and the number / label / title read from a heading.
  2. Where a finding ends: the next heading no deeper than its own; deeper
     headings stay inside; a nested finding heading is a sub-block that
     keeps the outer finding's fields.
  3. How a field line is recognised: bold or plain label, colon inside or
     outside the bold, list markers, quotes; unknown bold labels in extra.
  4. Case and whitespace: labels and enumerated values in any case, token
     separators, annotations, placeholders; unrecognised values are None.
  5. Values never span lines.
  Plus: comments and fences, report-level fields, the scenario fixtures
  under tests/fixtures/tester_reports/scenarios/, the parse_report input
  forms, and the CLI / wrapper JSON equalities.

Subprocesses run with an environment built from an explicit set, a
sandboxed kanban root under tmp_path, and a working directory outside the
repository.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import textwrap

import pytest

from pgai_agent_kanban.reports import tester_report
from pgai_agent_kanban.reports.tester_report import (
    ENUMERATED_FIELDS,
    Finding,
    parse_report,
    parse_report_text,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

# This file lives at team/tests/unit/, so two parents up is team/.
_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCENARIOS = _TEAM_DIR / "tests" / "fixtures" / "tester_reports" / "scenarios"
_WRAPPER = _TEAM_DIR / "scripts" / "lib" / "tester_report.sh"
_TEMPLATE = _TEAM_DIR / "templates" / "agent" / "REPORT-TEMPLATE.md"
_MODULE = "pgai_agent_kanban.reports.tester_report"
_SCENARIO_FILES = sorted(_SCENARIOS.glob("*.md"))


def _parse(text: str) -> "tester_report.TesterReport":
    return parse_report_text(textwrap.dedent(text))


def _only(text: str) -> Finding:
    report = _parse(text)
    assert len(report.findings) == 1, [f.heading for f in report.findings]
    return report.findings[0]


def _scenario(name: str) -> "tester_report.TesterReport":
    return parse_report(_SCENARIOS / name)


# ---------------------------------------------------------------------------
# Rule 1 -- what starts a finding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hashes", ["##", "###", "####", "#####", "######"])
def test_finding_heading_at_levels_two_to_six_opens_a_finding(hashes: str) -> None:
    finding = _only(f"# Report\n\n{hashes} Finding 1: a title\n**Category:** bug\n")

    assert (finding.level, finding.number, finding.category) == (len(hashes), 1, "bug")


@pytest.mark.parametrize(
    "heading",
    ["## Findings", "### Findings summary", "# Finding 1: report-title level",
     "####### Finding 1: seven hashes", "### Step 7", "### A Finding 1"],
)
def test_heading_that_is_not_a_singular_finding_word_at_levels_two_to_six_opens_nothing(
    heading: str,
) -> None:
    report = _parse(f"{heading}\n**Category:** bug\n**Filed As:** BUG-0001\n")

    assert report.findings == []


def test_missing_space_after_hashes_is_allowed_only_for_a_finding_heading() -> None:
    report = _parse(
        """\
        ###Finding 1: no space
        **Category:** bug
        ###Step 7 is not a heading, so finding 1 continues
        **Fix Effort:** large
        """
    )

    assert len(report.findings) == 1
    assert (report.findings[0].category, report.findings[0].fix_effort) == ("bug", "large")


@pytest.mark.parametrize(
    "heading, number, label, title",
    [
        ("### Finding 1: a title", 1, "1", "a title"),
        ("### Finding 12 -- double dash", 12, "12", "double dash"),
        ("### Finding 3 — Step 6.4: em dash", 3, "3", "Step 6.4: em dash"),
        ("### Finding: no number", None, None, "no number"),
        ("### Finding A: a letter label", None, "A", "a letter label"),
        ("### Finding #6: hash label", 6, "6", "hash label"),
        ("### finding 4", 4, "4", ""),
        ("### FINDING 5. upper case", 5, "5", "upper case"),
        ("### **Finding 7:** bold heading", 7, "7", "bold heading"),
        ("### Finding 8: closing hashes ###", 8, "8", "closing hashes"),
        ("### Finding 9 (behavioural): annotated", 9, "9", "(behavioural): annotated"),
        ("### Finding about a defect", None, None, "about a defect"),
    ],
)
def test_number_label_and_title_are_read_from_the_finding_heading(
    heading: str, number, label, title: str
) -> None:
    finding = _only(f"{heading}\nbody\n")

    assert (finding.number, finding.label, finding.title) == (number, label, title)


# ---------------------------------------------------------------------------
# Rule 2 -- where a finding ends
# ---------------------------------------------------------------------------


def test_finding_ends_at_the_next_heading_no_deeper_than_its_own() -> None:
    report = _parse(
        """\
        ### Finding 1: a
        **Filed As:** BUG-0001

        ### Step 7
        **Filed As:** none
        **Systemic Risk:** high

        ## Lint Findings (non-blocking)
        **Category:** bug
        """
    )

    (finding,) = report.findings
    assert finding.filed_as == "BUG-0001"
    assert finding.systemic_risk is None and finding.category is None
    assert finding.body == "**Filed As:** BUG-0001"


def test_deeper_non_finding_heading_stays_inside_the_finding() -> None:
    finding = _only(
        """\
        ### Finding 1: a
        **Category:** gap

        ##### Evidence
        **Systemic Risk:** medium
        """
    )

    assert (finding.category, finding.systemic_risk) == ("gap", "medium")
    assert "##### Evidence" in finding.body


def test_thematic_break_and_setext_underline_end_nothing() -> None:
    finding = _only(
        """\
        ### Finding 1: a
        **Category:** bug
        Some text
        ---
        **Fix Effort:** small

        ***
        **Systemic Risk:** low
        """
    )

    assert (finding.category, finding.fix_effort, finding.systemic_risk) == ("bug", "small", "low")


def test_nested_finding_is_a_sub_block_that_keeps_the_outer_finding_fields() -> None:
    report = _scenario("nested-finding-keeps-outer-fields.md")

    assert [f.number for f in report.findings] == [1, 3]
    outer = report.findings[0]
    assert (outer.category, outer.fix_effort, outer.systemic_risk) == ("bug", "small", "high")
    assert (outer.regression, outer.integrity, outer.filed_as) == ("no", "none", "BUG-0003")
    # The sub-block's own fields are its own, never the outer finding's.
    assert outer.severity is None
    (inner,) = outer.sub_findings
    assert (inner.number, inner.level, inner.category, inner.severity) == (2, 4, "pass", "low")
    assert "#### Finding 2: inner detail" in outer.body
    assert (report.findings[1].category, report.findings[1].systemic_risk) == ("gap", "low")


def test_outer_finding_without_a_field_does_not_take_it_from_a_sub_block() -> None:
    finding = _only(
        """\
        ### Finding 1: a grouping finding
        Two parts follow.

        #### Finding 1a: part one
        **Category:** pass
        **Systemic Risk:** high
        """
    )

    assert finding.category is None and finding.systemic_risk is None
    assert [(s.label, s.category, s.systemic_risk) for s in finding.sub_findings] == [
        ("1a", "pass", "high")
    ]


def test_outer_finding_lines_after_its_sub_block_are_its_own() -> None:
    finding = _only(
        """\
        ### Finding 1: outer
        **Category:** bug

        #### Finding 2: inner
        **Category:** pass

        #### Notes
        **Filed As:** BUG-0007
        """
    )

    assert (finding.category, finding.filed_as) == ("bug", "BUG-0007")
    assert finding.sub_findings[0].filed_as is None


def test_findings_under_level_four_five_and_six_headings_are_parsed() -> None:
    report = _scenario("deeper-finding-headings.md")

    assert [(f.number, f.level) for f in report.findings] == [(1, 4), (2, 5), (3, 6)]
    first, second, third = report.findings
    assert (first.category, first.systemic_risk, first.filed_as) == ("bug", "high", "BUG-0001")
    assert (first.regression, first.integrity) == ("no", "none")
    assert (second.category, second.systemic_risk, second.filed_as) == ("gap", "medium", "BUG-0002")
    assert (third.category, third.filed_as) == ("stale-assertion", "PRIORITY-0004")


# ---------------------------------------------------------------------------
# Heading variants scenario
# ---------------------------------------------------------------------------


def test_heading_variants_each_follow_the_documented_rule() -> None:
    report = _scenario("heading-variants.md")

    got = [(f.number, f.label, f.level, f.title, f.category) for f in report.findings]
    assert got == [
        (1, "1", 3, "no space after the hashes", "bug"),
        (None, None, 3, "a title with no number", "gap"),
        (4, "4", 3, "lowercase word, double-dash separator", "pass"),
        (5, "5", 2, "a level-two finding", "stale-assertion"),
    ]
    # "### Findings summary" opened nothing and ended finding 1, so its
    # Category line is no finding's; seven hashes is body text of finding 5.
    assert report.findings[0].systemic_risk == "high"
    assert report.findings[3].severity == "low"


# ---------------------------------------------------------------------------
# Rule 3 -- how a field line is recognised
# ---------------------------------------------------------------------------


def test_field_line_variants_read_the_same_values() -> None:
    report = _scenario("field-line-variants.md")

    assert len(report.findings) == 7
    for finding in report.findings:
        got = (finding.category, finding.fix_effort, finding.systemic_risk,
               finding.regression, finding.integrity, finding.filed_as)
        assert got == ("bug", "small", "high", "no", "none", "BUG-0003"), finding.heading


@pytest.mark.parametrize(
    "line",
    [
        "**Systemic Risk:** high",
        "**Systemic Risk**: high",
        "__Systemic Risk:__ high",
        "*Systemic Risk:* high",
        "**Systemic Risk: high**",
        "Systemic Risk: high",
        "- **Systemic Risk:** high",
        "  * Systemic Risk: high",
        "3. **Systemic Risk:** high",
        '"Systemic Risk": "high"',
        "**Systemic Risk:** `high`",
        "**systemic_risk:** HIGH",
        "**Systemic-Risk** : High.",
    ],
)
def test_each_field_line_shape_reads_the_field(line: str) -> None:
    assert _only(f"### Finding 1: a\n{line}\n").systemic_risk == "high"


@pytest.mark.parametrize(
    "line",
    ["> **Systemic Risk:** high", "| **Systemic Risk** | high |", "Systemic Risk high"],
)
def test_quoted_table_and_colonless_lines_are_not_field_lines(line: str) -> None:
    finding = _only(f"### Finding 1: a\n{line}\n")

    assert finding.systemic_risk is None and "systemic_risk" not in finding.raw


def test_other_bold_labels_land_in_extra_and_plain_ones_are_prose() -> None:
    finding = _only(
        """\
        ### Finding 1: a
        **Category:** bug
        **Integrity Damage:** no
        - **Traces to:** `team/scripts/x.sh:12`
        **RC branch / commit:** ai_rc/v9.9.9 at abc1234
        Note: a plain sentence with a colon.
        **Empty label:**
        """
    )

    assert finding.extra == {
        "integrity_damage": "no",
        "traces_to": "team/scripts/x.sh:12",
        "rc_branch_commit": "ai_rc/v9.9.9 at abc1234",
    }
    assert finding.integrity is None


def test_first_field_line_with_a_value_decides_the_field() -> None:
    finding = _only(
        """\
        ### Finding 1: a
        **Filed As:**
        **Filed As:** BUG-0004
        **Filed As:** BUG-0005
        **Category:** pre-existing-bug
        **Category:** bug
        **Traces to:** first
        **Traces to:** second
        """
    )

    assert finding.filed_as == "BUG-0004"
    assert finding.category == "pre-existing-bug" and finding.raw["category"] == "pre-existing-bug"
    assert finding.extra == {"traces_to": "first"}


@pytest.mark.parametrize(
    "value, expected",
    [
        ("BUG-0058", "BUG-0058"),
        ("bug-58 (cited)", "BUG-0058"),
        ("see BUG-0058-a-slug and BUG-0059", "BUG-0058"),
        ("BUG-0601 (Path C-A — filed as BUG-0601)", "BUG-0601"),
        ("PRIORITY-0012 (Path C-B)", "PRIORITY-0012"),
        ("none", None),
        ("not filed — pass finding", None),
        ("BUG-NNNN or PRIORITY-NNNN", None),
    ],
)
def test_filed_as_reads_the_first_bug_or_priority_id(value: str, expected) -> None:
    finding = _only(f"### Finding 1: a\n**Filed As:** {value}\n")

    assert finding.filed_as == expected
    assert finding.raw["filed_as"] == value


def test_filed_by_is_read_as_written() -> None:
    finding = _only("### Finding 1: a\n**Filed By:** TESTER (autonomous Path C)\n")

    assert finding.filed_by == "TESTER (autonomous Path C)"


# ---------------------------------------------------------------------------
# Rule 4 -- case and whitespace; enumerated values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field, value, expected",
    [
        ("Category", "pass-with-caveat", "pass-with-caveat"),
        ("Category", "Pass with caveat", "pass-with-caveat"),
        ("Category", "stale_assertion", "stale-assertion"),
        ("Category", "bug (pre-existing)", "bug"),
        ("Category", "pass (not applicable)", "pass"),
        ("Category", "pre-existing-bug", "pre-existing-bug"),
        ("Category", "Pre-existing bug (predates the RC)", "pre-existing-bug"),
        ("Category", "pre_existing_bug", "pre-existing-bug"),
        ("Category", "pre-existing-bugs", None),
        ("Category", "skipped (trigger condition not met)", None),
        ("Fix Effort", "small (1-2 CODER tasks)", "small"),
        ("Fix Effort", "small-to-medium (2-3 CODER tasks)", None),
        ("Fix Effort", "small / medium / large", None),
        ("Systemic Risk", "Medium — could recur", "medium"),
        ("Systemic Risk", "low (isolated) / medium (could recur) / high (broad)", None),
        ("Systemic Risk", "unknown", None),
        ("Regression", "Yes (this release introduced it)", "yes"),
        ("Regression", "yes|no", None),
        ("Integrity", "Breakage (gated unit suite fails)", "breakage"),
        ("Integrity", "destructive.", "destructive"),
        ("Severity", "CRITICAL", "critical"),
        ("Severity", "quality. Unit suite only", None),
    ],
)
def test_enumerated_value_is_a_lowercase_token_or_none(field: str, value: str, expected) -> None:
    finding = _only(f"### Finding 1: a\n**{field}:** {value}\n")
    attr = field.lower().replace(" ", "_")

    assert getattr(finding, attr) == expected
    assert expected is None or expected in ENUMERATED_FIELDS[attr]


def test_pre_existing_bug_is_its_own_category_and_a_longer_word_is_not() -> None:
    """``pre-existing-bug`` reads as its own category token -- never as
    ``bug``, never None -- while a token running on into more letters
    (``pre-existing-bugs``) is still not that token and reads None."""
    own = _only("### Finding 1: a\n**Category:** pre-existing-bug\n")
    longer = _only("### Finding 1: a\n**Category:** pre-existing-bugs\n")

    assert own.category == "pre-existing-bug"
    assert longer.category is None and longer.raw["category"] == "pre-existing-bugs"


def test_category_vocabulary_is_every_category_the_tester_role_defines() -> None:
    """Each finding category TESTER's role file lists is a token the parser
    reads as itself."""
    categories = ("pass", "pass-with-caveat", "gap", "bug", "pre-existing-bug", "stale-assertion")

    assert set(ENUMERATED_FIELDS["category"]) == set(categories)
    for category in categories:
        assert _only(f"### Finding 1: a\n**Category:** {category}\n").category == category


def test_absent_enumerated_fields_are_none() -> None:
    finding = _only("### Finding 1: a\nNo fields at all.\n")

    for attr in ("filed_as", "filed_by", "severity", "category", "regression",
                 "integrity", "fix_effort", "systemic_risk"):
        assert getattr(finding, attr) is None, attr
    assert finding.raw == {} and finding.extra == {}


@pytest.mark.parametrize("label", ["Fix Effort", "fix effort", "FIX  EFFORT", "fix_effort", "Fix-Effort", "Fix\tEffort"])
def test_label_case_and_separators_do_not_matter(label: str) -> None:
    assert _only(f"### Finding 1: a\n**{label}:** large\n").fix_effort == "large"


# ---------------------------------------------------------------------------
# Rule 5 -- values never span lines
# ---------------------------------------------------------------------------


def test_value_ends_at_the_line_break() -> None:
    finding = _only(
        """\
        ### Finding 1: a
        **Filed By:** TESTER
        continued prose directly under the field line
        **Traces to:** one line
        and not this one
        """
    )

    assert finding.filed_by == "TESTER"
    assert finding.extra == {"traces_to": "one line"}


def test_value_on_the_line_after_an_empty_label_is_not_read() -> None:
    finding = _only("### Finding 1: a\n**Filed As:**\nBUG-0003\n")

    assert finding.filed_as is None and "filed_as" not in finding.raw


# ---------------------------------------------------------------------------
# Comments and fenced code
# ---------------------------------------------------------------------------


def test_quoted_or_inline_code_comment_opener_hides_no_finding_or_field() -> None:
    report = _scenario("quoted-comment-opener.md")

    assert [f.number for f in report.findings] == [1, 2]
    first, second = report.findings
    assert (first.category, first.systemic_risk, first.filed_as) == ("bug", "high", "BUG-0001")
    # The real, closed comment still hides the field line inside it.
    assert first.severity is None
    assert (second.category, second.filed_as) == ("gap", "BUG-0002")
    assert (report.recommendation, report.systemic_risk) == ("ship-with-concerns", "high")
    assert "`<!--`" in report.summary


def test_fenced_code_hides_headings_and_field_lines() -> None:
    finding = _only(
        """\
        ### Finding 1: a
        **Category:** gap

        ```text
        ### Finding 2: an example inside a fence
        **Systemic Risk:** high
        ```
        """
    )

    assert finding.category == "gap" and finding.systemic_risk is None
    assert finding.sub_findings == []


def test_unclosed_fence_or_comment_opener_hides_nothing() -> None:
    report = _parse(
        """\
        ### Finding 1: after a stray fence
        ```
        **Category:** bug

        <!-- a comment opener that is never closed
        ### Finding 2: still read
        **Category:** gap
        """
    )

    assert [(f.number, f.category) for f in report.findings] == [(1, "bug"), (2, "gap")]


# ---------------------------------------------------------------------------
# Report-level fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "section, expected",
    [
        ("## Recommendation\n\nSHIP-WITH-CONCERNS\n", "ship-with-concerns"),
        ("## Recommendation\n**SHIP-WITH-SERIOUS-CONCERNS**\n", "ship-with-serious-concerns"),
        ("## Recommendation\n<!-- Allowed values: PASS | X -->\n**Decision: PASS**\n", "pass"),
        ("## Recommendation\n---\nRecommendation: pass — clean run\n", "pass"),
        ("## Recommendation: SHIP-WITH-CONCERNS\n", "ship-with-concerns"),
        ("## Recommendation\n**Decision: PASS | SHIP-WITH-CONCERNS**\n", None),
        ("## Recommendation\nShip it.\n", None),
        ("## Recommendation\n\n## Systemic Risk\nlow\n", None),
        ("## Verdict\nPASS\n", None),
    ],
)
def test_recommendation_is_read_from_its_own_section(section: str, expected) -> None:
    assert _parse(section).recommendation == expected


def test_report_systemic_risk_comes_from_its_section_never_from_findings() -> None:
    with_section = _parse("## Systemic Risk\n<!-- low | medium | high -->\n\nMedium\n")
    without = _parse("## Findings\n### Finding 1: a\n**Systemic Risk:** high\n")

    assert (with_section.systemic_risk, with_section.raw["systemic_risk"]) == ("medium", "Medium")
    assert without.systemic_risk is None and without.raw == {}


def test_summary_is_the_summary_section_text() -> None:
    report = _parse(
        """\
        # Verification Report

        ## Executive Summary

        First line. <!-- hidden -->
        Second line.

        ## Findings
        """
    )

    assert report.summary == "First line.\nSecond line."
    assert _parse("## Findings\n").summary is None


def test_report_template_placeholders_read_as_no_value() -> None:
    report = parse_report(_TEMPLATE)

    assert report.recommendation is None
    assert report.findings, "the template's example findings are findings"
    for finding in report.findings:
        assert (finding.category, finding.fix_effort, finding.systemic_risk, finding.filed_as) == (
            None, None, None, None
        ), finding.heading


# ---------------------------------------------------------------------------
# parse_report input forms
# ---------------------------------------------------------------------------


def test_parse_report_accepts_a_path_a_path_string_or_report_text(tmp_path: pathlib.Path) -> None:
    text = "## Systemic Risk\nlow\n\n### Finding 1: a\n**Category:** bug\n"
    report_file = tmp_path / "report.md"
    report_file.write_text(text, encoding="utf-8")

    by_path = parse_report(report_file)

    assert by_path == parse_report(str(report_file)) == parse_report(text)
    assert parse_report("") == tester_report.TesterReport(None, None, None, [], {})


def test_parse_report_raises_for_a_missing_path_and_a_wrong_type(tmp_path: pathlib.Path) -> None:
    with pytest.raises(OSError):
        parse_report(str(tmp_path / "absent.md"))
    with pytest.raises(TypeError):
        parse_report(42)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# CLI and shell wrapper
# ---------------------------------------------------------------------------


def _env(tmp_path: pathlib.Path, *, pythonpath: "str | None" = None) -> dict:
    kanban_root = tmp_path / "kanban"
    temp_dir = tmp_path / "tmp"
    kanban_root.mkdir(exist_ok=True)
    temp_dir.mkdir(exist_ok=True)
    extra = {
        "KANBAN_ROOT": str(kanban_root),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
    }
    if pythonpath is not None:
        extra["PYTHONPATH"] = pythonpath
    return build_scrubbed_env(home_dir=tmp_path / "home", extra_env=extra)


def _run(argv: list, tmp_path: pathlib.Path, env: dict, stdin: str = "") -> subprocess.CompletedProcess:
    workdir = tmp_path / "elsewhere"
    workdir.mkdir(exist_ok=True)
    return subprocess.run(
        argv, cwd=str(workdir), env=env, input=stdin,
        capture_output=True, text=True, timeout=60, check=False,
    )


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def test_scenario_fixtures_exist() -> None:
    assert len(_SCENARIO_FILES) >= 5, f"expected the scenario reports under {_SCENARIOS}"


@pytest.mark.parametrize("report", _SCENARIO_FILES, ids=lambda p: p.stem)
def test_cli_json_and_shell_wrapper_json_equal_the_parse_result(
    report: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    expected = parse_report(report)

    module = _run([sys.executable, "-m", _MODULE, str(report), "--json"], tmp_path,
                  _env(tmp_path, pythonpath=str(_TEAM_DIR)))
    direct = _run([str(_WRAPPER), str(report), "--json"], tmp_path, _env(tmp_path))
    sourced = _run(["bash", "-c", 'set -euo pipefail; source "$1"; tester_report_json "$2"',
                    "tester_report_json", str(_WRAPPER), str(report)], tmp_path, _env(tmp_path))

    assert module.returncode == 0, _diagnostic(module)
    assert json.loads(module.stdout) == expected.to_dict(), _diagnostic(module)
    assert module.stdout == expected.to_json(), _diagnostic(module)
    assert direct.returncode == 0 and direct.stdout == module.stdout, _diagnostic(direct)
    assert sourced.returncode == 0 and sourced.stdout == module.stdout, _diagnostic(sourced)


def test_cli_reads_standard_input_and_prints_a_text_summary(tmp_path: pathlib.Path) -> None:
    text = (_SCENARIOS / "nested-finding-keeps-outer-fields.md").read_text(encoding="utf-8")

    result = _run([str(_WRAPPER), "-"], tmp_path, _env(tmp_path), stdin=text)

    assert result.returncode == 0, _diagnostic(result)
    assert result.stdout.splitlines()[:3] == [
        "recommendation: none", "systemic_risk: high", "findings: 2",
    ], _diagnostic(result)
    assert "finding 1 [category=bug systemic_risk=high fix_effort=small filed_as=BUG-0003]: outer" in result.stdout


def test_cli_exits_one_naming_an_unreadable_report(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "absent" / "report.md"

    result = _run([str(_WRAPPER), str(missing), "--json"], tmp_path, _env(tmp_path))

    assert result.returncode == 1, _diagnostic(result)
    assert str(missing) in result.stderr and result.stdout == "", _diagnostic(result)


def test_wrapper_json_function_requires_a_report_path(tmp_path: pathlib.Path) -> None:
    result = _run(["bash", "-c", 'source "$1"; tester_report_json', "tester_report_json", str(_WRAPPER)],
                  tmp_path, _env(tmp_path))

    assert result.returncode == 2, _diagnostic(result)
    assert "report path is required" in result.stderr, _diagnostic(result)


def test_wrapper_help_prints_the_parser_usage(tmp_path: pathlib.Path) -> None:
    result = _run([str(_WRAPPER), "--help"], tmp_path, _env(tmp_path))

    assert result.returncode == 0, _diagnostic(result)
    assert "--json" in result.stdout and "report" in result.stdout, _diagnostic(result)
