"""
test_changelog_writer_date_line.py
==================================
The CHANGELOG writer (team/pgai_agent_kanban/cm/changelog_writer.py) takes
a release's date from a notes file's ``**Release Date:**`` line, or from a
``**Date:**`` line when there is none. A notes file with neither still
renders, dated ``unknown``, and the writer prints exactly one warning on
stderr naming that file -- a missing date never stops the CHANGELOG step.

All paths live under pytest's tmp_path; the CLI runs by file path from a
working directory outside the repository with an environment built from
an explicit set.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import textwrap

from pgai_agent_kanban.cm.changelog_writer import parse_release_notes, regenerate
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_WRITER = _TEAM_DIR / "pgai_agent_kanban" / "cm" / "changelog_writer.py"


def _repo(tmp_path: pathlib.Path, notes: dict[str, str]) -> tuple[pathlib.Path, pathlib.Path]:
    repo_root = tmp_path / "repo"
    notes_dir = repo_root / "release-notes"
    notes_dir.mkdir(parents=True)
    for version, date_line in notes.items():
        (notes_dir / f"{version}.md").write_text(
            textwrap.dedent(
                f"""\
                # Release Notes: test-project {version}

                {date_line}
                **Released By:** test

                ## Summary
                Release {version}.

                ## Features
                None
                """
            ),
            encoding="utf-8",
        )
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    return repo_root, bugs_dir


def test_date_line_supplies_the_entry_date(tmp_path: pathlib.Path, capsys) -> None:
    repo_root, bugs_dir = _repo(tmp_path, {"v1.2.3": "**Date:** 2026-01-02"})

    content = regenerate(repo_root, ["v1.2.3"], [], bugs_dir)

    assert "## v1.2.3 — 2026-01-02" in content, content
    assert capsys.readouterr().err == ""


def test_release_date_line_wins_over_date_line(tmp_path: pathlib.Path) -> None:
    repo_root, _ = _repo(
        tmp_path, {"v1.2.3": "**Release Date:** 2026-03-04\n**Date:** 2026-01-02"}
    )

    entry = parse_release_notes("v1.2.3", repo_root / "release-notes" / "v1.2.3.md")

    assert entry.date == "2026-03-04"


def test_missing_date_renders_unknown_with_one_warning_naming_the_file(
    tmp_path: pathlib.Path,
) -> None:
    repo_root, bugs_dir = _repo(
        tmp_path, {"v1.2.3": "**Released On:** someday", "v1.2.2": "**Release Date:** 2026-01-01"}
    )
    notes_path = repo_root / "release-notes" / "v1.2.3.md"
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()

    result = subprocess.run(
        [sys.executable, str(_WRITER), str(repo_root), str(bugs_dir)],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=build_scrubbed_env(home_dir=tmp_path / "home"),
        timeout=60,
    )

    diagnostic = f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    assert result.returncode == 0, diagnostic
    assert "## v1.2.3 — unknown" in result.stdout, diagnostic
    assert "## v1.2.2 — 2026-01-01" in result.stdout, diagnostic
    warnings = [line for line in result.stderr.splitlines() if line.strip()]
    assert len(warnings) == 1, diagnostic
    assert str(notes_path) in warnings[0], diagnostic
