"""
test_tester_report_differential.py
==================================
The legacy TESTER-report readers run over the report corpus, the
differential report that records where they disagree, and the parser held
to both.

The legacy readers are the test-only copies under
tests/fixtures/tester_reports/legacy_readers/, frozen as the readers stood
before they moved onto pgai_agent_kanban.reports.tester_report;
legacy_readers/differential.py runs them and compares their readings.
tests/fixtures/tester_reports/differential.md lists, report by report,
every finding field two of them read differently, each reader's value,
the resolved value and a one-line reason. These tests hold that file and
the parser to the readers:

  1. Every consumer whose fields the "stop_bug_files" column stands for --
     the Known Issues stop list, the Bugs Skipped table, the ship-policy
     dims and values modes -- produces on every corpus report exactly what
     that one reading predicts, and OVERWATCH's findings_all_filed agrees
     with the resolution_matrix reading. So one column per reading path is
     every reader, and no two consumers can disagree unseen.
  2. differential.md lists exactly the disagreements the readers have, with
     the values they read, and every row carries a resolved value and a
     reason.
  3. parse_report, and the report's golden file, hold each row's resolved
     value.
  4. Everywhere the legacy readers agree -- the finding split and every
     field they read in common or alone -- parse_report reads the same:
     finding split, category, systemic_risk, fix_effort, regression,
     integrity, filed_as, and the report-level recommendation and
     systemic_risk.

When a test here fails, its message prints the differential.md rows that
fix the file, in the file's own table format.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import pathlib
import re
from typing import Optional

import pytest

from pgai_agent_kanban.reports.tester_report import parse_report
from tests.fixtures.tester_reports.legacy_readers import differential as legacy
from tests.fixtures.tester_reports.legacy_readers import resolution_matrix as legacy_matrix
from tests.fixtures.tester_reports.legacy_readers import ship_policy_report as legacy_ship_policy
from tests.fixtures.tester_reports.legacy_readers import skipped_bug_pairs as legacy_skipped
from tests.fixtures.tester_reports.legacy_readers import stop_bug_files as legacy_stop

# This file lives at team/tests/unit/, so two parents up is team/.
_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_FIXTURES = _TEAM_DIR / "tests" / "fixtures" / "tester_reports"
_CORPUS = _FIXTURES / "corpus"
_GOLDEN = _FIXTURES / "golden"
_DIFFERENTIAL = _FIXTURES / "differential.md"
_CORPUS_FILES = sorted(_CORPUS.glob("*.md"))

_ABSENT_CELL = "(absent)"
_NOT_READ_CELL = "-"
_REPORT_HEADING_RE = re.compile(r"^\s*#{3}\s+`?(?P<name>[^`\s]+\.md)`?\s*$")
_CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")
_SEPARATOR_CELL_RE = re.compile(r"^:?-+:?$")
_BUG_ID_RE = re.compile(r"BUG-(\d{4,})", re.IGNORECASE)


# ---------------------------------------------------------------------------
# differential.md
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _Row:
    report: str
    finding: Optional[int]
    field: str
    values: dict
    resolved: str
    reason: str
    line: int

    @property
    def key(self) -> tuple:
        return (self.report, self.finding, self.field)


def _cells(line: str) -> list:
    """The cells of a Markdown table row, trimmed, ``\\|`` unescaped."""
    inner = line.strip()
    inner = inner[1:] if inner.startswith("|") else inner
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return [cell.strip().replace("\\|", "|") for cell in _CELL_SPLIT_RE.split(inner)]


def _cell_value(cell: str):
    return None if cell == _ABSENT_CELL else cell


def _read_differential(path: pathlib.Path) -> tuple:
    """``(rows, problems)``: every disagreement row of differential.md, and a
    note for each table row that could not be read as one."""
    rows: list = []
    problems: list = []
    report = None
    header = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        heading = _REPORT_HEADING_RE.match(line)
        if heading:
            report, header = heading.group("name"), None
            continue
        if report is None or not line.strip().startswith("|"):
            continue
        cells = _cells(line)
        if header is None:
            header = [cell.lower() for cell in cells]
            continue
        if all(_SEPARATOR_CELL_RE.match(cell) for cell in cells):
            continue
        if len(cells) != len(header):
            problems.append(f"line {number}: {len(cells)} cells under a {len(header)}-column header")
            continue
        cell = dict(zip(header, cells))
        finding_cell = cell.get("finding", "")
        values = {
            reader: _cell_value(cell[reader])
            for reader in legacy.READERS
            if reader in cell and cell[reader] != _NOT_READ_CELL
        }
        rows.append(_Row(
            report=report,
            finding=int(finding_cell) if finding_cell.isdigit() else None,
            field=cell.get("field", ""),
            values=values,
            resolved=cell.get("resolved", ""),
            reason=cell.get("reason", ""),
            line=number,
        ))
    return rows, problems


@functools.lru_cache(maxsize=None)
def _differential() -> tuple:
    return _read_differential(_DIFFERENTIAL)


def _rows() -> list:
    return _differential()[0]


def _row_param_id(row: _Row) -> str:
    where = f"finding{row.finding}" if row.finding is not None else "report"
    return f"{pathlib.Path(row.report).stem}-{where}-{row.field.replace(' ', '_')}"


def _row_text(disagreement: legacy.Disagreement) -> str:
    """A disagreement as a differential.md row, its Heading cell left for the
    author and its Resolved and Reason cells to fill in."""
    values = dict(disagreement.values)
    cells = [
        str(disagreement.finding) if disagreement.finding is not None else _NOT_READ_CELL,
        "",
        disagreement.field,
        *(
            (_ABSENT_CELL if values[reader] is None else str(values[reader]))
            if reader in values else _NOT_READ_CELL
            for reader in (legacy.STOP_BUG_FILES, legacy.RESOLUTION_MATRIX)
        ),
        "<resolved value>",
        "<one-line reason>",
    ]
    return f"{disagreement.report}: | " + " | ".join(cells) + " |"


# ---------------------------------------------------------------------------
# Legacy readings, the parser and the goldens
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=None)
def _readings(report_name: str) -> dict:
    return legacy.legacy_readings(_CORPUS / report_name)


@functools.lru_cache(maxsize=None)
def _computed_disagreements() -> dict:
    return {
        disagreement.key: disagreement
        for path in _CORPUS_FILES
        for disagreement in legacy.disagreements(path.name, _readings(path.name))
    }


def _parsed_value(findings: list, report_fields, finding: Optional[int], field: str):
    """A field from a parse (Finding objects or golden JSON dicts): the
    finding count for the split, a report-level field, or a finding's."""
    def get(obj, name):
        return obj[name] if isinstance(obj, dict) else getattr(obj, name)

    flat = legacy.flattened_findings(findings)
    if field == legacy.SPLIT_FIELD:
        return len(flat)
    if finding is None:
        return get(report_fields, field)
    if finding > len(flat):
        return f"<no finding {finding}: the parse has {len(flat)}>"
    return get(flat[finding - 1], field)


def _resolved_matches(value, resolved: str) -> bool:
    return (resolved == _ABSENT_CELL and value is None) or (value is not None and str(value) == resolved)


@pytest.fixture(scope="module")
def corpus_bugs_dir(tmp_path_factory) -> pathlib.Path:
    """A bugs/ directory holding one file for every BUG id the corpus names,
    so the bug-file resolvers resolve every id they read and their output
    is exactly the ids they picked."""
    bugs = tmp_path_factory.mktemp("bugs")
    for path in _CORPUS_FILES:
        for match in _BUG_ID_RE.finditer(path.read_text(encoding="utf-8")):
            (bugs / f"BUG-{int(match.group(1)):04d}.md").touch()
    return bugs


def _ordered_unique(ids) -> list:
    seen: list = []
    for bug_id in ids:
        if bug_id and bug_id not in seen:
            seen.append(bug_id)
    return seen


# ---------------------------------------------------------------------------
# 1. Each consumer reads through the reading its column records
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("report_path", [pytest.param(p, id=p.stem) for p in _CORPUS_FILES])
def test_each_legacy_consumer_reads_through_its_recorded_reading(
    report_path: pathlib.Path, corpus_bugs_dir: pathlib.Path, capsys
) -> None:
    readings = _readings(report_path.name)
    shared = readings[legacy.STOP_BUG_FILES].findings
    matrix = readings[legacy.RESOLUTION_MATRIX].findings
    report, bugs = str(report_path), str(corpus_bugs_dir)
    mismatches = []

    for cause, field, wanted in (
        (legacy_stop.CAUSE_SYSTEMIC_RISK_HIGH, "systemic_risk", "high"),
        (legacy_stop.CAUSE_FIX_EFFORT_LARGE, "fix_effort", "large"),
    ):
        expected = _ordered_unique(f["filed_as"] for f in shared if f[field] == wanted)
        got = [p.stem for p in legacy_stop.stop_bug_paths(report, bugs, cause)]
        if got != expected:
            mismatches.append(f"Known Issues stop list ({cause}): {got}, the reading predicts {expected}")

    skipped = [f for f in shared if f["category"] in legacy_skipped.SKIPPED_CATEGORIES]
    expected_ids = _ordered_unique(f["filed_as"] for f in skipped)
    pairs = legacy_skipped.resolve_skipped_bug_pairs(report, bugs)
    got_ids = [pathlib.Path(bug_file).stem for bug_file, _rationale in pairs]
    if got_ids != expected_ids:
        mismatches.append(f"Bugs Skipped ids: {got_ids}, the reading predicts {expected_ids}")
    else:
        by_id = {}
        for finding in skipped:
            by_id.setdefault(finding["filed_as"], finding)
        for bug_id, (_bug_file, rationale) in zip(got_ids, pairs):
            finding = by_id[bug_id]
            introduced = finding["category"] != "stale-assertion" and finding["regression"] == "yes"
            if rationale.startswith("Introduced by this release") != introduced:
                mismatches.append(
                    f"Bugs Skipped rationale for {bug_id}: {rationale!r} does not follow "
                    f"category={finding['category']} regression={finding['regression']}"
                )

    regression, integrity = "unknown", "unknown"
    for finding in shared:
        if finding["systemic_risk"] != "high":
            continue
        if finding["regression"] == "yes":
            regression = "yes"
        elif finding["regression"] == "no" and regression != "yes":
            regression = "no"
        if finding["integrity"] in ("breakage", "destructive"):
            integrity = finding["integrity"]
        elif finding["integrity"] == "none" and integrity not in ("breakage", "destructive"):
            integrity = "none"
    dims = legacy_ship_policy.finding_dimensions(report)
    if dims != f"{regression} {integrity}":
        mismatches.append(f"ship-policy dims: {dims!r}, the reading predicts {regression} {integrity}")

    large = "yes" if any(f["fix_effort"] == "large" for f in shared) else "no"
    values = legacy_ship_policy.report_values(report)
    if values["has_large_fix_effort"] != large:
        mismatches.append(f"ship-policy has_large_fix_effort: {values['has_large_fix_effort']}, the reading predicts {large}")

    parsed = legacy_matrix.parse_tester_report_findings(report_path.read_text(encoding="utf-8"))
    all_filed = all(f["filed_as"] is not None for f in matrix)
    if legacy_matrix.findings_all_filed(parsed) != all_filed:
        mismatches.append(f"OVERWATCH findings_all_filed: {not all_filed}, the reading predicts {all_filed}")

    capsys.readouterr()  # the readers' own stderr narration is not under test
    assert mismatches == [], (
        f"{report_path.name}: a legacy consumer no longer reads the way its differential "
        f"column records ({legacy.__name__} module docstring), so differential.md could miss "
        "a disagreement: " + "; ".join(mismatches)
    )


# ---------------------------------------------------------------------------
# 2. differential.md lists exactly the disagreements, with reasons
# ---------------------------------------------------------------------------


def test_differential_rows_all_read() -> None:
    rows, problems = _differential()
    assert problems == [], f"{_DIFFERENTIAL.name} has table rows that are not disagreement rows: {problems}"
    assert rows, f"{_DIFFERENTIAL.name} holds no disagreement rows; it should list the corpus disagreements"
    keys = [row.key for row in rows]
    duplicates = sorted({key for key in keys if keys.count(key) > 1}, key=str)
    assert duplicates == [], f"{_DIFFERENTIAL.name} lists these (report, finding, field) more than once: {duplicates}"


def test_differential_lists_exactly_the_reports_where_legacy_readers_disagree() -> None:
    computed = _computed_disagreements()
    listed = {row.key: row for row in _rows()}

    missing = [computed[key] for key in computed if key not in listed]
    assert missing == [], (
        f"the legacy readers disagree where {_DIFFERENTIAL.name} lists nothing. Add each row "
        "below under its report's heading, with the resolved value and a one-line reason:\n"
        + "\n".join(_row_text(d) for d in missing)
    )
    extra = sorted((row for key, row in listed.items() if key not in computed), key=lambda r: r.line)
    assert extra == [], (
        f"{_DIFFERENTIAL.name} lists disagreements the legacy readers do not have; delete these rows: "
        + ", ".join(f"line {row.line} ({row.report} finding {row.finding} {row.field})" for row in extra)
    )
    assert sorted({key[0] for key in listed}) == sorted({key[0] for key in computed})


def test_differential_rows_carry_each_readers_value() -> None:
    computed = _computed_disagreements()
    wrong = []
    for row in _rows():
        disagreement = computed.get(row.key)
        if disagreement is None:
            continue  # reported by the exact-listing test
        expected = {reader: value for reader, value in disagreement.values}
        if row.field == legacy.SPLIT_FIELD:
            row_values = {reader: (int(v) if v and v.isdigit() else v) for reader, v in row.values.items()}
        else:
            row_values = row.values
        if row_values != expected:
            wrong.append(f"line {row.line}: lists {row.values}, the readers read {expected} -> {_row_text(disagreement)}")
    assert wrong == [], f"{_DIFFERENTIAL.name} rows whose reader values are not what the readers read:\n" + "\n".join(wrong)


def test_every_differential_row_carries_a_resolution_and_a_reason() -> None:
    unresolved = [
        f"line {row.line} ({row.report} finding {row.finding} {row.field})"
        for row in _rows()
        if not row.resolved or not row.reason or row.reason.startswith("<")
    ]
    assert unresolved == [], (
        f"{_DIFFERENTIAL.name} rows with no resolved value or no one-line reason; fill in the "
        "Resolved and Reason cells: " + ", ".join(unresolved)
    )


# ---------------------------------------------------------------------------
# 3. The parser and the goldens hold each resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("row", [pytest.param(row, id=_row_param_id(row)) for row in _rows()])
def test_parse_report_and_golden_hold_the_resolved_value(row: _Row) -> None:
    report = parse_report(_CORPUS / row.report)
    parsed = _parsed_value(report.findings, report, row.finding, row.field)
    assert _resolved_matches(parsed, row.resolved), (
        f"{_DIFFERENTIAL.name} line {row.line} resolves {row.report} finding {row.finding} "
        f"{row.field} to {row.resolved!r}, but parse_report reads {parsed!r}. Either the parser "
        "changed how this report reads (fix the parser), or the resolution is wrong (fix the row)."
    )

    golden_path = _GOLDEN / f"{pathlib.Path(row.report).stem}.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    held = _parsed_value(golden["findings"], golden, row.finding, row.field)
    assert _resolved_matches(held, row.resolved), (
        f"{golden_path.name} holds {held!r} for finding {row.finding} {row.field}, not the "
        f"resolved {row.resolved!r} ({_DIFFERENTIAL.name} line {row.line}). Regenerate the golden "
        "as test_tester_report_corpus.py's docstring describes."
    )


# ---------------------------------------------------------------------------
# 4. Where the legacy readers agree, the parser reads the same
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("report_path", [pytest.param(p, id=p.stem) for p in _CORPUS_FILES])
def test_parse_report_reads_like_the_legacy_readers_where_they_agree(report_path: pathlib.Path) -> None:
    readings = _readings(report_path.name)
    listed = {row.key for row in _rows() if row.report == report_path.name}
    report = parse_report(report_path)
    flat = legacy.flattened_findings(report.findings)
    differences = []

    if legacy.split_agrees(readings) and (report_path.name, None, legacy.SPLIT_FIELD) not in listed:
        legacy_headings = [h for h, _body in legacy_stop.finding_sections(report_path.read_text(encoding="utf-8"))]
        parser_headings = [finding.heading for finding in flat]
        if parser_headings != legacy_headings:
            differences.append(
                f"finding split: the legacy readers split {len(legacy_headings)} findings "
                f"{legacy_headings}, parse_report {len(parser_headings)} {parser_headings}"
            )
    if not differences:
        for (finding, field), legacy_value in legacy.agreed_values(readings).items():
            if (report_path.name, finding, field) in listed:
                continue
            value = _parsed_value(report.findings, report, finding, field)
            if value != legacy_value:
                where = f"finding {finding}" if finding is not None else "report"
                differences.append(f"{where} {field}: legacy readers {legacy_value!r}, parse_report {value!r}")

    assert differences == [], (
        f"{report_path.name}: parse_report reads differently from the legacy readers where they "
        "agree, which no differential row allows. Fix the parser, or -- if the legacy readers are "
        f"wrong here -- this is not a reader disagreement and needs a decision outside "
        f"{_DIFFERENTIAL.name}: " + "; ".join(differences)
    )
