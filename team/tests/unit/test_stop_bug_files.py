"""
test_stop_bug_files.py
======================
Unit tests for pgai_agent_kanban.cm.stop_bug_files -- the resolver that
finds the bug files behind the TESTER findings that caused a ship-policy
stop, for the Known Issues block of a release the ship posture shipped
through.

Coverage:
  - systemic-risk-high keeps only the findings whose own Systemic Risk is
    high; fix-effort-large keeps only the findings whose own Fix Effort is
    large; a finding's field never leaks into its neighbour's.
  - The findings come from parse_report through report_findings: finding
    headings at level 3 and deeper are read, a deeper non-finding heading
    stays inside its finding, a shallower heading ends it, and the level-2
    "## Findings" heading opens no finding.
  - A finding heading nested inside a finding is read as a sub-finding of
    its own, after the outer finding, and the outer finding keeps every
    one of its own fields (and still reaches the stop list); a field line
    after the nested finding closes never overrides a finding's own.
  - Field lines are read liberally (bold, backticks, case, an annotation
    after the first word): bold and plain labels alike, backticks ignored,
    the label's colon required, a rating read as its leading token, and
    the Filed As id found anywhere in the value. filed_bug_id names only
    a BUG id, never a priority. stop_bug_paths keeps the path form the
    bugs directory was given in, and so does the CLI's --keep-path-form.
  - Each bug is returned once, in report order; a Filed As value that does
    not resolve to a file is skipped with a warning naming it; a finding
    with no Filed As, a missing report and an unknown cause behave as
    documented.
  - The CLI prints one path per line and exits 0 when nothing resolves.

Every test works on files under tmp_path only.
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.cm.skipped_bug_pairs import resolve_skipped_bug_pairs
from pgai_agent_kanban.cm.stop_bug_files import (
    CAUSE_FIX_EFFORT_LARGE,
    CAUSE_SYSTEMIC_RISK_HIGH,
    filed_bug_id,
    main,
    report_findings,
    resolve_stop_bug_files,
    stop_bug_paths,
)


def _bugs(tmp_path: pathlib.Path, *ids: str) -> pathlib.Path:
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir(exist_ok=True)
    for bug_id in ids:
        (bugs_dir / f"{bug_id}-some-defect.md").write_text(f"# {bug_id}\n", encoding="utf-8")
    return bugs_dir


def _report(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    path = tmp_path / "report.md"
    path.write_text(body, encoding="utf-8")
    return path


def _findings(tmp_path: pathlib.Path, body: str) -> list:
    """report_findings over *body* written as tmp_path/report.md."""
    return report_findings(str(_report(tmp_path, body)))


_FLAT = """# Verification Report

## Findings

### Finding 1: Unit lane aborts

**Fix Effort:** small
**Systemic Risk:** high (dead signal)
**Filed As:** BUG-0042 (Path C-A)

### Finding 2: Inline Python above target

**Fix Effort:** large (architectural)
**Systemic Risk:** low
**Filed As:** BUG-0043

### Finding 3: Another high finding, same bug

**Fix Effort:** Large
**Systemic Risk:** `High`
**Filed As:** bug-0042

## Lint Findings (non-blocking)

**Systemic Risk:** high
**Filed As:** BUG-0099
"""


def test_systemic_risk_high_keeps_only_high_risk_findings_once_in_order(tmp_path):
    bugs_dir = _bugs(tmp_path, "BUG-0042", "BUG-0043", "BUG-0099")
    paths = resolve_stop_bug_files(str(_report(tmp_path, _FLAT)), str(bugs_dir), CAUSE_SYSTEMIC_RISK_HIGH)
    assert [pathlib.Path(p).name for p in paths] == ["BUG-0042-some-defect.md"]


def test_fix_effort_large_keeps_only_large_fix_findings(tmp_path):
    bugs_dir = _bugs(tmp_path, "BUG-0042", "BUG-0043")
    paths = resolve_stop_bug_files(str(_report(tmp_path, _FLAT)), str(bugs_dir), CAUSE_FIX_EFFORT_LARGE)
    # Findings 2 and 3, both large-fix, in report order; finding 3 names its bug in lower case.
    assert [pathlib.Path(p).name for p in paths] == ["BUG-0043-some-defect.md", "BUG-0042-some-defect.md"]


def test_paths_are_absolute(tmp_path):
    bugs_dir = _bugs(tmp_path, "BUG-0042")
    paths = resolve_stop_bug_files(str(_report(tmp_path, _FLAT)), str(bugs_dir), CAUSE_SYSTEMIC_RISK_HIGH)
    assert all(pathlib.Path(p).is_absolute() for p in paths)


def test_nested_finding_headings_are_findings_and_stop_at_their_own_level(tmp_path):
    text = (
        "## Findings\n\n"
        "### Release mechanics\n\n"
        "#### Finding 1: nested one\n"
        "**Systemic Risk:** high\n"
        "##### detail sub-heading\n"
        "**Filed As:** BUG-0001\n"
        "#### Finding 2: nested two\n"
        "**Systemic Risk:** low\n"
        "### Other area\n"
        "**Systemic Risk:** high\n"
    )
    findings = _findings(tmp_path, text)
    assert len(findings) == 2
    assert findings[0].filed_as == "BUG-0001" and "nested two" not in findings[0].body
    assert "Other area" not in findings[1].body and findings[1].systemic_risk == "low"


_NESTED_FINDING = """## Findings

### Finding 1: outer
**Fix Effort:** large
**Systemic Risk:** high
**Filed As:** BUG-0003

#### Finding 2: inner detail
**Category:** pass

## Lint Findings (non-blocking)
None.
"""


def test_nested_finding_heading_is_its_own_finding_and_the_outer_keeps_its_fields(tmp_path):
    """A finding heading inside a finding is read as a finding of its own,
    right after the outer one, and neither carries the other's fields: the
    outer keeps its own Systemic Risk and Filed As, and the nested
    finding's Category never becomes the outer's."""
    findings = _findings(tmp_path, _NESTED_FINDING)
    assert len(findings) == 2
    outer, inner = findings
    assert (outer.systemic_risk, outer.filed_as, outer.category) == ("high", "BUG-0003", None)
    assert (inner.category, inner.filed_as) == ("pass", None)
    assert "Lint Findings" not in inner.body


@pytest.mark.parametrize("cause", [CAUSE_SYSTEMIC_RISK_HIGH, CAUSE_FIX_EFFORT_LARGE])
def test_nested_finding_heading_keeps_the_outer_finding_on_the_stop_list(tmp_path, cause):
    bugs_dir = _bugs(tmp_path, "BUG-0003")
    paths = resolve_stop_bug_files(str(_report(tmp_path, _NESTED_FINDING)), str(bugs_dir), cause)
    assert [pathlib.Path(p).name for p in paths] == ["BUG-0003-some-defect.md"]


def test_line_after_a_closed_nested_finding_never_overrides_a_finding(tmp_path):
    """A field line below a later heading as deep as a closed nested
    finding never becomes any finding's own value."""
    text = (
        "### Finding 1: outer\n"
        "**Systemic Risk:** low\n"
        "#### Finding 2: inner\n"
        "**Systemic Risk:** low\n"
        "#### Notes\n"
        "**Systemic Risk:** high\n"
        "### Finding 3: next\n"
        "**Systemic Risk:** low\n"
    )
    findings = _findings(tmp_path, text)
    assert len(findings) == 3
    assert all(finding.systemic_risk != "high" for finding in findings)


def test_level_two_findings_heading_never_opens_a_finding(tmp_path):
    assert _findings(tmp_path, "## Findings\n**Systemic Risk:** high\n") == []


def test_findings_summary_heading_opens_no_finding_and_lends_no_field(tmp_path):
    text = (
        "## Findings\n\n"
        "### Findings summary\n"
        "**Systemic Risk:** high\n"
        "**Filed As:** BUG-0001\n\n"
        "### Finding 1: the real one\n"
        "**Systemic Risk:** low\n"
    )
    findings = _findings(tmp_path, text)
    assert len(findings) == 1
    assert findings[0].filed_as is None and findings[0].systemic_risk == "low"


def test_unresolvable_filed_as_is_skipped_with_a_warning(tmp_path, capsys):
    bugs_dir = _bugs(tmp_path)
    paths = resolve_stop_bug_files(str(_report(tmp_path, _FLAT)), str(bugs_dir), CAUSE_SYSTEMIC_RISK_HIGH)
    assert paths == []
    err = capsys.readouterr().err
    assert "BUG-0042 (Path C-A)" in err and str(bugs_dir) in err


def test_finding_without_filed_as_contributes_nothing(tmp_path):
    bugs_dir = _bugs(tmp_path, "BUG-0001")
    report = _report(tmp_path, "### Finding 1: unfiled\n**Systemic Risk:** high\n**Filed As:** none\n")
    assert resolve_stop_bug_files(str(report), str(bugs_dir), CAUSE_SYSTEMIC_RISK_HIGH) == []


def test_bare_bug_file_name_resolves(tmp_path):
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    (bugs_dir / "BUG-0007.md").write_text("# BUG-0007\n", encoding="utf-8")
    report = _report(tmp_path, "### Finding 1: x\n**Fix Effort:** large\n**Filed As:** BUG-0007\n")
    paths = resolve_stop_bug_files(str(report), str(bugs_dir), CAUSE_FIX_EFFORT_LARGE)
    assert [pathlib.Path(p).name for p in paths] == ["BUG-0007.md"]


def test_missing_report_resolves_to_nothing(tmp_path):
    assert resolve_stop_bug_files(str(tmp_path / "absent.md"), str(_bugs(tmp_path)), CAUSE_SYSTEMIC_RISK_HIGH) == []


def test_unknown_cause_is_refused(tmp_path):
    with pytest.raises(ValueError, match="systemic-risk-high"):
        resolve_stop_bug_files(str(_report(tmp_path, _FLAT)), str(_bugs(tmp_path)), "regression")


def test_cli_prints_one_path_per_line(tmp_path, capsys):
    bugs_dir = _bugs(tmp_path, "BUG-0042", "BUG-0043")
    assert main([str(_report(tmp_path, _FLAT)), str(bugs_dir), CAUSE_FIX_EFFORT_LARGE]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [pathlib.Path(p).name for p in lines] == ["BUG-0043-some-defect.md", "BUG-0042-some-defect.md"]


def test_cli_exits_zero_when_nothing_resolves(tmp_path, capsys):
    assert main([str(tmp_path / "absent.md"), str(_bugs(tmp_path)), CAUSE_SYSTEMIC_RISK_HIGH]) == 0
    assert capsys.readouterr().out == ""


def test_cli_refuses_an_unknown_cause(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        main([str(tmp_path / "r.md"), str(tmp_path), "regression"])
    assert excinfo.value.code == 2


_PROBE_HEADING = "### Finding 1: field probe\n"


@pytest.mark.parametrize(
    "line",
    [
        "**Filed As:** BUG-0003 (cited)",
        "Filed As: BUG-0003 (cited)",
        "**Filed As**: BUG-0003 (cited)",
        "  filed as : `BUG-0003` (cited)  ",
        "**Filed  As:**   BUG-0003 (cited)",
    ],
)
def test_filed_as_reads_bold_plain_and_backticked_field_lines_alike(tmp_path, line):
    (finding,) = _findings(tmp_path, f"{_PROBE_HEADING}prose\n{line}\n")
    assert filed_bug_id(finding) == "BUG-0003"


@pytest.mark.parametrize(
    "block",
    [
        "**Filed As** BUG-0003\n",
        "The finding was Filed As BUG-0003 last week.\n",
        "**Filed As:**\n",
        "",
    ],
)
def test_filed_as_needs_the_label_its_colon_and_a_value(tmp_path, block):
    (finding,) = _findings(tmp_path, f"{_PROBE_HEADING}{block}")
    assert filed_bug_id(finding) == ""


def test_the_first_field_line_with_a_value_decides_the_field(tmp_path):
    block = "**Systemic Risk:**\n**Systemic Risk:** low\n**Systemic Risk:** high\n"
    (finding,) = _findings(tmp_path, f"{_PROBE_HEADING}{block}")
    assert finding.systemic_risk == "low"


@pytest.mark.parametrize(
    "value, rating",
    [
        ("high", "high"),
        ("High.", "high"),
        ("`High`", "high"),
        ("high (dead signal)", "high"),
        ("high—breaks the dashboard", "high"),
        ("(unrated)", None),
    ],
)
def test_a_rating_reads_as_its_leading_token(tmp_path, value, rating):
    for label in ("**Systemic Risk:**", "Systemic Risk:", "systemic risk:"):
        (finding,) = _findings(tmp_path, f"{_PROBE_HEADING}{label} {value}\n")
        assert finding.systemic_risk == rating


@pytest.mark.parametrize(
    "value, category",
    [("stale_assertion", "stale-assertion"), ("pass-with-caveat", "pass-with-caveat")],
)
def test_a_category_token_reads_with_dashes(tmp_path, value, category):
    (finding,) = _findings(tmp_path, f"{_PROBE_HEADING}**Category:** {value}\n")
    assert finding.category == category


@pytest.mark.parametrize(
    "value, bug_id",
    [
        ("BUG-0003", "BUG-0003"),
        ("`BUG-0003`", "BUG-0003"),
        ("see BUG-0003", "BUG-0003"),
        ("cites the existing BUG-0058-probe-defect (open)", "BUG-0058"),
        ("bug-42 then BUG-00042", "BUG-0042"),
        ("none", ""),
        ("not filed (pass)", ""),
        # differential.md, the filed_as rows: a finding filed as a priority
        # file names no bug file, so the Known Issues and Bugs Skipped lists
        # carry nothing for it.
        ("PRIORITY-0001", ""),
    ],
)
def test_filed_bug_id_finds_the_bug_id_anywhere_in_the_value(tmp_path, value, bug_id):
    (finding,) = _findings(tmp_path, f"{_PROBE_HEADING}**Filed As:** {value}\n")
    assert filed_bug_id(finding) == bug_id


def test_a_finding_filed_as_a_priority_reaches_neither_list_and_warns_nothing(tmp_path, capsys):
    """differential.md, the filed_as rows: the parser names the priority id,
    and a priority id names no bug file, so neither the stop list nor the
    Bugs Skipped table carries the finding, and nothing is narrated."""
    bugs_dir = _bugs(tmp_path, "BUG-0001")
    report = _report(
        tmp_path,
        "### Finding 1: filed as a priority\n**Category:** gap\n"
        "**Systemic Risk:** high\n**Filed As:** PRIORITY-0001\n",
    )
    assert resolve_stop_bug_files(str(report), str(bugs_dir), CAUSE_SYSTEMIC_RISK_HIGH) == []
    assert resolve_skipped_bug_pairs(str(report), str(bugs_dir)) == []
    assert capsys.readouterr().err == ""


def test_stop_bug_paths_keep_the_bugs_dir_form_and_resolve_matches_them(tmp_path, monkeypatch):
    _bugs(tmp_path, "BUG-0042", "BUG-0043")
    report = _report(tmp_path, _FLAT)
    monkeypatch.chdir(tmp_path)
    kept = stop_bug_paths(str(report), "bugs", CAUSE_FIX_EFFORT_LARGE)
    assert [str(p) for p in kept] == ["bugs/BUG-0043-some-defect.md", "bugs/BUG-0042-some-defect.md"]
    assert resolve_stop_bug_files(str(report), "bugs", CAUSE_FIX_EFFORT_LARGE) == [
        str(p.resolve()) for p in kept
    ]


def test_cli_keep_path_form_prints_stop_bug_paths(tmp_path, monkeypatch, capsys):
    _bugs(tmp_path, "BUG-0042", "BUG-0043")
    report = _report(tmp_path, _FLAT)
    monkeypatch.chdir(tmp_path)
    assert main(["--keep-path-form", str(report), "bugs", CAUSE_FIX_EFFORT_LARGE]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "bugs/BUG-0043-some-defect.md",
        "bugs/BUG-0042-some-defect.md",
    ]


def test_plain_and_cited_field_lines_reach_the_stop_list(tmp_path):
    bugs_dir = _bugs(tmp_path, "BUG-0003")
    report = _report(
        tmp_path,
        "### Finding 1: plain labels\nSystemic Risk: high\nFiled As: see `BUG-0003`\n",
    )
    paths = resolve_stop_bug_files(str(report), str(bugs_dir), CAUSE_SYSTEMIC_RISK_HIGH)
    assert [pathlib.Path(p).name for p in paths] == ["BUG-0003-some-defect.md"]
