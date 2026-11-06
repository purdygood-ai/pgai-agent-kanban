"""
test_docs_team_path_citations.py
================================
``check_docs_citations.py --team-paths`` (through
``docs_citation_scan.find_missing_team_paths``) reports every ``team/<path>``
a document in docs/ or HOW_TO.md names that does not exist in the tree:

  1. A missing file and a missing directory are reported with the citing
     file and line; an existing path, one ending in sentence punctuation,
     a placeholder or glob pattern, and a ``team/`` inside a longer path
     are not.
  2. HOW_TO.md and nested docs/ pages are scanned.
  3. The command line lists each finding with a re-check command and exits
     0 (quality class), and says ok when there is nothing to report.
  4. A path a document declares illustrative on a
     ``<!-- team-paths: example team/<path> -->`` line is not reported there,
     in prose or inside a fenced block, nor is anything under it; a stale
     factual citation in the same document, and the same path in a document
     that does not declare it, still are.

Every tree is built under tmp_path.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

from pgai_agent_kanban.lib.docs_citation_scan import (
    MissingTeamPath,
    declared_example_team_paths,
    find_missing_team_paths,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = _TEAM_DIR / "scripts" / "check_docs_citations.py"


def _tree(root: pathlib.Path) -> pathlib.Path:
    (root / "team" / "scripts").mkdir(parents=True)
    (root / "team" / "scripts" / "real.sh").write_text("", encoding="utf-8")
    (root / "docs" / "api").mkdir(parents=True)
    (root / "docs" / "guide.md").write_text(
        "Run team/scripts/real.sh.\n"
        "The old team/scripts/retired.sh is gone, and so is team/gone-dir/.\n"
        "Patterns: team/workflows/<type>/workflow.sh and team/scripts/*.sh.\n"
        "Elsewhere: projects/demo/team/notes.md and $TEAM/x.\n",
        encoding="utf-8",
    )
    (root / "docs" / "api" / "nested.md").write_text("See (team/pgai_agent_kanban/old.py).\n", encoding="utf-8")
    (root / "HOW_TO.md").write_text("Step: `team/scripts/moved.sh --help`\n", encoding="utf-8")
    return root


def test_missing_paths_are_reported_with_file_and_line(tmp_path) -> None:
    root = _tree(tmp_path / "repo")

    findings = find_missing_team_paths(root)

    assert findings == [
        MissingTeamPath("docs/api/nested.md", 1, "team/pgai_agent_kanban/old.py"),
        MissingTeamPath("docs/guide.md", 2, "team/scripts/retired.sh"),
        MissingTeamPath("docs/guide.md", 2, "team/gone-dir/"),
        MissingTeamPath("HOW_TO.md", 1, "team/scripts/moved.sh"),
    ]


def test_the_command_line_lists_findings_and_exits_zero(tmp_path) -> None:
    root = _tree(tmp_path / "repo")

    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--team-paths", "--repo-root", str(root)],
        env=build_scrubbed_env(home_dir=tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "4 team/<path> citation(s) name nothing in the tree" in result.stdout, result.stdout
    assert "  - HOW_TO.md:1 cites 'team/scripts/moved.sh'" in result.stdout, result.stdout
    assert f"--team-paths --repo-root {root}" in result.stdout, result.stdout


def test_a_clean_tree_reports_ok(tmp_path) -> None:
    root = tmp_path / "repo"
    (root / "team").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "docs" / "a.md").write_text("Everything lives in team/.\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--team-paths", "--repo-root", str(root)],
        env=build_scrubbed_env(home_dir=tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "team-paths: ok" in result.stdout, result.stdout


def test_declared_example_paths_are_not_reported_but_stale_ones_are(tmp_path) -> None:
    root = tmp_path / "repo"
    (root / "team" / "workflows").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "docs" / "walkthrough.md").write_text(
        "# Walkthrough\n"
        "\n"
        "<!-- team-paths: example team/workflows/my-new-type/ — the placeholder plugin -->\n"
        "<!--team-paths:EXAMPLE team/tests/unit/test_state_heavy.py, team/internal/ -->\n"
        "\n"
        "The generator writes `team/workflows/my-new-type/`.\n"
        "```bash\n"
        "source team/workflows/my-new-type/workflow.sh\n"
        "python3 -m pytest team/tests/unit/test_state_heavy.py   # BAD\n"
        "```\n"
        "No test may import from `team/internal/`.\n"
        "The retired team/scripts/retired.sh is gone.\n"
        "A sibling team/workflows/my-new-type-two/ is not covered.\n",
        encoding="utf-8",
    )
    (root / "docs" / "other.md").write_text("See team/workflows/my-new-type/workflow.sh.\n", encoding="utf-8")

    findings = find_missing_team_paths(root)

    assert findings == [
        MissingTeamPath("docs/other.md", 1, "team/workflows/my-new-type/workflow.sh"),
        MissingTeamPath("docs/walkthrough.md", 12, "team/scripts/retired.sh"),
        MissingTeamPath("docs/walkthrough.md", 13, "team/workflows/my-new-type-two/"),
    ]


def test_a_declaration_lists_team_paths_up_to_its_reason() -> None:
    text = (
        "<!-- team-paths: example team/a/ team/b.py, — team/c/ is only mentioned in the reason -->\n"
        "<!-- doc-lint: skip — team/d/ is not a declaration -->\n"
    )

    assert declared_example_team_paths(text) == {"team/a", "team/b.py"}
