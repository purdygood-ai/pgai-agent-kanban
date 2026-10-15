"""
test_ship_policy_bug_filer_release_pattern.py
=============================================
Unit tests for the release-pattern kind of
pgai_agent_kanban.cm.ship_policy_bug_filer -- the bug the release step files
when the last three releases' notes all read NON-FUNCTIONAL and the ship
posture lets the release ship anyway.

Coverage:
  - The filed bug follows the BUG-TEMPLATE.md shape (Status open, Category
    cm, a canonical severity, a self-documenting Filed By line), is named
    for the pattern, and quotes the three releases with their statuses and
    the release-notes directory they were read from.
  - A second detection while the first bug is open or running cites it; a
    closed one no longer suppresses a fresh filing.
  - The pattern dedup key never collides with an input-defect bug that
    happens to share the same field and source path.
  - The CLI accepts the kind and prints outcome/bug_id/bug_path lines.

Every assertion works on a tmp_path bugs/ directory.
"""

from __future__ import annotations

import pathlib

from pgai_agent_kanban.cm.ship_policy_bug_filer import (
    KIND_RELEASE_PATTERN,
    KIND_REPORT_VALUE,
    file_or_cite_input_bug,
    main,
)

_RELEASES = "v1.1.0.md=NON-FUNCTIONAL, v1.2.0.md=NON-FUNCTIONAL, v1.3.0.md=NON-FUNCTIONAL"
_NOTES_DIR = "/dev-tree/release-notes"


def _file(bugs_dir: pathlib.Path, **overrides):
    kwargs = dict(
        project="demo",
        version="v1.4.0",
        kind=KIND_RELEASE_PATTERN,
        field="Status",
        raw_value=_RELEASES,
        source_path=_NOTES_DIR,
        filed_at="2026-09-01",
    )
    kwargs.update(overrides)
    return file_or_cite_input_bug(str(bugs_dir), **kwargs)


def test_pattern_bug_names_the_pattern_and_quotes_the_releases(tmp_path):
    bugs_dir = tmp_path / "bugs"
    result = _file(bugs_dir)

    assert result.outcome == "filed"
    files = sorted(bugs_dir.glob("BUG-*.md"))
    assert len(files) == 1
    assert files[0].name.endswith("-last-three-releases-non-functional.md")
    text = files[0].read_text(encoding="utf-8")
    assert "## Status\nopen\n" in text
    assert "## Category\ncm\n" in text
    assert "**Severity:** medium" in text
    assert "**Filed By:** cm-release ship-policy (autonomous) -- " in text
    assert "all marked NON-FUNCTIONAL" in text
    assert _RELEASES in text
    assert _NOTES_DIR in text
    assert "v1.4.0" in text


def test_second_detection_cites_the_open_pattern_bug(tmp_path):
    bugs_dir = tmp_path / "bugs"
    first = _file(bugs_dir)
    second = _file(bugs_dir, version="v1.5.0", raw_value="v1.2.0.md=NON-FUNCTIONAL, v1.3.0.md=NON-FUNCTIONAL, v1.4.0.md=NON-FUNCTIONAL")

    assert second.outcome == "duplicate"
    assert second.bug_id == first.bug_id
    assert len(list(bugs_dir.glob("BUG-*.md"))) == 1


def test_closed_pattern_bug_no_longer_suppresses_a_new_one(tmp_path):
    bugs_dir = tmp_path / "bugs"
    first = _file(bugs_dir)
    path = pathlib.Path(first.bug_path)
    path.write_text(path.read_text(encoding="utf-8").replace("## Status\nopen", "## Status\ndone"), encoding="utf-8")

    second = _file(bugs_dir, version="v1.5.0")
    assert second.outcome == "filed"
    assert second.bug_id != first.bug_id


def test_pattern_key_is_distinct_from_an_input_defect_key(tmp_path):
    bugs_dir = tmp_path / "bugs"
    defect = _file(bugs_dir, kind=KIND_REPORT_VALUE)
    pattern = _file(bugs_dir)
    assert pattern.outcome == "filed"
    assert pattern.bug_id != defect.bug_id


def test_cli_accepts_the_release_pattern_kind(tmp_path, capsys):
    bugs_dir = tmp_path / "bugs"
    rc = main([str(bugs_dir), "demo", "v1.4.0", KIND_RELEASE_PATTERN, "Status", _RELEASES, _NOTES_DIR])
    assert rc == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "outcome=filed"
    assert lines[1].startswith("bug_id=BUG-")
    assert lines[2].startswith("bug_path=") and lines[2].endswith("-last-three-releases-non-functional.md")
