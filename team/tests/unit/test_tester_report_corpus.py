"""
test_tester_report_corpus.py
============================
The TESTER report parser, pgai_agent_kanban.reports.tester_report, held to
the reports TESTER actually wrote. Every file under
tests/fixtures/tester_reports/corpus/ is a historical TESTER report.md. For
each one, parse_report must not raise, and its JSON must equal the
committed golden file of the same stem under
tests/fixtures/tester_reports/golden/. The tests read only these committed
files, never a live kanban root.

The corpus
----------
147 distinct reports, collected read-only on 2026-10-09 from three source
globs and limited to the kanban's own reports and the pgai-chomp-man demo's.
The kept reports matched 155 files; the 8 byte-identical duplicates were
copied once.

  $PGAI_AGENT_KANBAN_ROOT_PATH/projects/*/tasks/TESTER-*/artifacts/report.md
      The live install, reinstalled that day: 8 pgai-agent-kanban files,
      each byte-identical to a copy in the pre-reinstall root below.
  $HOME/old_pgai_agent_kanban/projects/*/tasks/TESTER-*/artifacts/report.md
      The pre-reinstall root: 22 files, 8 pgai-agent-kanban and 14
      pgai-chomp-man.
  $HOME/old_pgai_agent_kanban/archive/*/tasks/TESTER-*/artifacts/report.md
      The pre-reinstall archive: 125 files, all pgai-agent-kanban.

By project: 133 pgai-agent-kanban, 14 pgai-chomp-man. Each file is named
``<project>--<task folder>.md`` and holds the report verbatim except for
four scrubs: an absolute path under the home-directory root reads ``$HOME``
in place of its home-directory prefix, a personal email address reads
``operator@example.invalid``, a project name from outside the kanban and
its demos reads ``my-app``, and a container directory path reads
``/opt/requirements``.

Regenerating the golden files
-----------------------------
A golden file records what parse_report returns for its report; nobody
hand-edits one. After an intentional change to the parser's rules,
regenerate every golden with the parser's own CLI, whose ``--json`` output
is ``TesterReport.to_json()``, the exact form the goldens are written in.
From the repository root:

    cd team
    for report in tests/fixtures/tester_reports/corpus/*.md; do
        python3 -m pgai_agent_kanban.reports.tester_report "$report" --json \\
            > "tests/fixtures/tester_reports/golden/$(basename "$report" .md).json"
    done

Then read ``git diff -- tests/fixtures/tester_reports/golden/``: each
changed golden is a real report the change now reads differently, and the
diff is the record of how. Commit the parser change and its goldens
together, so a report-format change is one module plus its goldens. A
report added to the corpus gets its golden from the same loop, and its
count goes into _CORPUS_COUNT and the paragraph above.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from pgai_agent_kanban.reports.tester_report import parse_report

# This file lives at team/tests/unit/, so two parents up is team/.
_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_FIXTURES = _TEAM_DIR / "tests" / "fixtures" / "tester_reports"
_CORPUS = _FIXTURES / "corpus"
_GOLDEN = _FIXTURES / "golden"
_CORPUS_FILES = sorted(_CORPUS.glob("*.md"))

# The number of distinct reports the corpus was collected with (see the
# module docstring). A file that goes missing fails here rather than
# silently shrinking the parametrized set below.
_CORPUS_COUNT = 147

# The home-directory root whose absolute paths the corpus is scrubbed of.
_HOME_ROOT = "/home/"


def _golden_for(report_path: pathlib.Path) -> pathlib.Path:
    return _GOLDEN / f"{report_path.stem}.json"


def test_corpus_holds_every_collected_report() -> None:
    assert len(_CORPUS_FILES) == _CORPUS_COUNT, (
        f"{_CORPUS} holds {len(_CORPUS_FILES)} reports, expected {_CORPUS_COUNT}. "
        "Restore the missing report from git, or, when a report was added or "
        "removed on purpose, update _CORPUS_COUNT and the module docstring's counts."
    )


def test_every_corpus_report_has_a_golden_and_every_golden_a_report() -> None:
    reports = {path.stem for path in _CORPUS_FILES}
    goldens = {path.stem for path in _GOLDEN.glob("*.json")}

    assert sorted(reports - goldens) == [], (
        f"corpus reports with no golden under {_GOLDEN}; generate them with the "
        "loop in this module's docstring"
    )
    assert sorted(goldens - reports) == [], (
        f"golden files with no corpus report under {_CORPUS}; delete them or restore their reports"
    )


def test_corpus_reports_carry_no_home_directory_path() -> None:
    carrying = [path.name for path in _CORPUS_FILES if _HOME_ROOT in path.read_text(encoding="utf-8")]

    assert carrying == [], (
        f"corpus reports still carrying an absolute home-directory path: {carrying}. "
        "Replace the home-directory prefix with $HOME in each, then regenerate their goldens."
    )


@pytest.mark.parametrize("report_path", [pytest.param(path, id=path.stem) for path in _CORPUS_FILES])
def test_corpus_report_parses_to_its_golden(report_path: pathlib.Path) -> None:
    report = parse_report(report_path)
    golden_path = _golden_for(report_path)
    golden = json.loads(golden_path.read_text(encoding="utf-8"))

    assert json.loads(report.to_json()) == golden, (
        f"{report_path.name} no longer parses to {golden_path.name}. If the parser "
        "change was intentional, regenerate the goldens as this module's docstring "
        "describes and review the diff; otherwise the change altered how a real "
        "TESTER report reads."
    )
