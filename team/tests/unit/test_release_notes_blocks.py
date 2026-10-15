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

Every fixture lives under tmp_path; nothing touches a kanban root.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

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
