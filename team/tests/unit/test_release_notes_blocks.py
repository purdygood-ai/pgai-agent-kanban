"""
test_release_notes_blocks.py
============================
The known-issue ship stage's four release-notes renderers and stamps
(team/scripts/lib/cm/release_known_issue.sh: build_known_issue_notes_block,
build_bugs_skipped_notes_block, cm_stamp_release_notes_status,
cm_stamp_known_issue_marker) run as commands of
pgai_agent_kanban.cm.release_notes_blocks, with no Python inside the bash.

Behaviour under test:
  1. Driven through the bash functions on one fixture set -- an ID-bearing
     Symptom, a ``|`` in a cell, a missing file, a Status placeholder behind
     a comment, carry-forward markers absent, comment-only and already set
     -- stdout, exit codes and every file written are byte-identical to the
     output recorded from the stage before its Python moved into the module.
  2. The inline-Python lint reports no heredoc and no ``python3 -c`` in
     release_known_issue.sh.
  3. A Bugs Skipped row's severity comes from ``## Severity``, else the
     ``**Severity:**`` header field, else UNSPECIFIED.
  4. A Symptom whose first period sits inside a backtick code span renders
     through the end of its real first sentence, and the release-notes and
     CHANGELOG first-sentence splits agree.
  5. A Symptom's commit id, RC branch name and task id never reach the row.

Every fixture lives under tmp_path; nothing touches a kanban root.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.cm import changelog_writer, release_notes_generator
from pgai_agent_kanban.cm.release_notes_blocks import bugs_skipped_rows
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_STAGE = "scripts/lib/cm/release_known_issue.sh"

_FIXTURES = {'BUG-0001-a.md': '# BUG-0001-a\n\n## Status\nopen\n\n## Severity\nmedium\nexplained further. ---\n\n## Public ID\n`KI-0042`\n\n## Symptom\n<!-- note -->\nThe widget | breaks when CODER-20260101-001-x runs.  Second sentence here.\n', 'BUG-0002-b.md': '# BUG-0002-b\n\n## Symptom\nNothing about BUG-0099 here\nover two lines\n\n## Known Issue Carry Forward\n<!-- set by CM -->\n', 'BUG-0003-c.md': '# BUG-0003-c\n\n## Known Issue Carry Forward\nv1.0.0\n', 'notes.md': '# Release v1.2.3\n\n## Status\n<!-- guidance -->\n\nPENDING-RELEASE\n\n## Known Issues\n\nNone.\n\n## Bugs Skipped\n\nNone.\n'}

# Recorded from the stage while its four helpers were inline Python.
_EXPECTED_STDOUT = '--KI\n- **KI-0042** — The widget | breaks when another item runs. — No workaround available.\n- Nothing about another item here over two lines (internal-only, not yet disclosed) — No workaround available.\nrc=0\n--BS\n| The widget \\| breaks when another item runs. | KI-0042 | MEDIUM | rationale \\| with another item |\n| Nothing about another item here over two lines | internal-only | UNSPECIFIED | r2 |\nrc=0\n--BSodd\nERROR: build_bugs_skipped_notes_block requires <bug_file> <rationale> pairs (got an odd number of arguments).\nrc=1\n--STAMP\n  Stamped ## Status: KNOWN-ISSUE in release-notes/notes.md\n  Stamped ## Known Issues in release-notes/notes.md\n  Stamped ## Bugs Skipped in release-notes/notes.md\nrc=0\n--STAMP2\nWARNING: PENDING-RELEASE placeholder not found under ## Status heading.\nrc=1\n--MARK\n[Step 4e] Known-issue carry-forward marker: 2 stamped, 1 already set.\nrc=0\n--MARK2\n[Step 4e] step 4e: already complete, continuing — known-issue carry-forward marker already set on 1 bug(s).\nrc=0\n'

_EXPECTED_FILES = {'BUG-0001-a.md': '# BUG-0001-a\n\n## Status\nopen\n\n## Severity\nmedium\nexplained further. ---\n\n## Public ID\n`KI-0042`\n\n## Symptom\n<!-- note -->\nThe widget | breaks when CODER-20260101-001-x runs.  Second sentence here.\n\n## Known Issue Carry Forward\nv1.2.3\n', 'BUG-0002-b.md': '# BUG-0002-b\n\n## Symptom\nNothing about BUG-0099 here\nover two lines\n\n## Known Issue Carry Forward\n<!-- set by CM -->\nv1.2.3\n\n', 'BUG-0003-c.md': '# BUG-0003-c\n\n## Known Issue Carry Forward\nv1.0.0\n', 'notes.md': '# Release v1.2.3\n\n## Status\n<!-- guidance -->\n\nKNOWN-ISSUE\n\n## Known Issues\n\n\n- ki line\n\n## Bugs Skipped\n\n\n| a | b |\n\n'}

_DRIVER = """\
set -uo pipefail
fx="$1"
source {stage}
echo "--KI"; build_known_issue_notes_block "$fx/BUG-0001-a.md" "$fx/BUG-0002-b.md" "$fx/missing.md"; echo "rc=$?"
echo "--BS"; build_bugs_skipped_notes_block "$fx/BUG-0001-a.md" "rationale | with CODER-20260101-002-y" "$fx/BUG-0002-b.md" "r2"; echo "rc=$?"
echo "--BSodd"; build_bugs_skipped_notes_block "$fx/BUG-0001-a.md" 2>&1; echo "rc=$?"
echo "--STAMP"; cm_stamp_release_notes_status "$fx/notes.md" KNOWN-ISSUE "- ki line" "| a | b |"; echo "rc=$?"
echo "--STAMP2"; cm_stamp_release_notes_status "$fx/notes.md" SHIPPED 2>&1; echo "rc=$?"
echo "--MARK"; cm_stamp_known_issue_marker v1.2.3 "$fx/BUG-0002-b.md" "$fx/BUG-0003-c.md" "$fx/BUG-0001-a.md"; echo "rc=$?"
echo "--MARK2"; cm_stamp_known_issue_marker v1.2.3 "$fx/BUG-0002-b.md"; echo "rc=$?"
"""


def test_stage_output_and_written_files_are_unchanged(tmp_path: pathlib.Path) -> None:
    fx = tmp_path / "fx"
    fx.mkdir()
    for name, text in _FIXTURES.items():
        (fx / name).write_text(text, encoding="utf-8")
    script = _DRIVER.format(stage=_STAGE).replace('fx="$1"', f'fx="{fx}"')

    result = run_bash(tmp_path, script)

    assert result.stdout.replace(str(fx), "FX") == _EXPECTED_STDOUT, result.stderr
    for name, text in _EXPECTED_FILES.items():
        assert (fx / name).read_text(encoding="utf-8") == text, name


def test_inline_python_lint_finds_no_python_in_the_stage() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/lint_inline_python.py", "--emit-allowlist"],
        cwd=_TEAM_DIR, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert "release_known_issue.sh" not in result.stdout, result.stdout


def _bug_file(tmp_path: pathlib.Path, header: str = "", sections: str = "", symptom: str = "It breaks.") -> str:
    """Write a bug file with *header* lines under its H1 and return its path."""
    path = tmp_path / "BUG-0001-x.md"
    path.write_text(
        f"# BUG-0001-x\n\n{header}\n\n## Status\nopen\n\n{sections}## Symptom\n{symptom}\n",
        encoding="utf-8",
    )
    return str(path)


def _row_cells(row: str) -> "list[str]":
    """The four cells of one rendered Bugs Skipped row."""
    return [cell.strip() for cell in row.strip().strip("|").split(" | ")]


@pytest.mark.parametrize(
    ("header", "sections", "expected"),
    [
        ("**Bug ID:** BUG-0001-x\n**Severity:** medium", "", "MEDIUM"),
        ("**Severity:** low.", "", "LOW"),
        ("**Severity:** low", "## Severity\nhigh\n\n", "HIGH"),
        ("**Bug ID:** BUG-0001-x", "", "UNSPECIFIED"),
    ],
    ids=["header-field-only", "header-field-trailing-period", "section-wins", "neither"],
)
def test_bugs_skipped_severity_reads_section_then_header_field(
    tmp_path: pathlib.Path, header: str, sections: str, expected: str
) -> None:
    row = bugs_skipped_rows([_bug_file(tmp_path, header, sections), "why"])

    assert _row_cells(row)[2] == expected


_CODE_SPAN_SYMPTOM = "Running `pvg build . --scene 2` fails. More text."
_CODE_SPAN_SENTENCE = "Running `pvg build . --scene 2` fails."


def test_bugs_skipped_substance_does_not_end_inside_a_code_span(tmp_path: pathlib.Path) -> None:
    row = bugs_skipped_rows([_bug_file(tmp_path, symptom=_CODE_SPAN_SYMPTOM), "why"])

    assert _row_cells(row)[0] == _CODE_SPAN_SENTENCE


@pytest.mark.parametrize(
    "text",
    [
        _CODE_SPAN_SYMPTOM,
        "Plain first. Second.",
        "Ends `with.` a span. Then more.",
        "Double ``a ` b. c`` span! Next.",
        "An unmatched ` backtick. Next.",
        "No terminal punctuation",
        "  spread\nover   lines.  Next ",
        "",
    ],
    ids=["code-span", "plain", "span-then-period", "double-backtick", "unmatched", "none", "multiline", "empty"],
)
def test_release_notes_and_changelog_first_sentence_agree(text: str) -> None:
    notes = release_notes_generator._first_sentence(text)

    assert changelog_writer._first_sentence(text) == notes
    if text == _CODE_SPAN_SYMPTOM:
        assert notes == _CODE_SPAN_SENTENCE


def test_bugs_skipped_substance_carries_no_commit_branch_or_task_id(tmp_path: pathlib.Path) -> None:
    symptom = "On ai_rc/v1.2.3 the unit suite (a1b2c3d4) fails, as TESTER-20260101-001 found."
    row = bugs_skipped_rows([_bug_file(tmp_path, symptom=symptom), "why"])

    substance = _row_cells(row)[0]
    for token in ("a1b2c3d4", "ai_rc/v1.2.3", "TESTER-20260101-001", "()"):
        assert token not in substance
    assert substance == "On the release candidate the unit suite fails, as another item found."
