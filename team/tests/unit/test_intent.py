"""
test_intent.py
================
Unit tests for pgai_agent_kanban.overwatch.intent — the read-only reader
for a project's INTENT.md declared-intent ledger.

Coverage:
  - The shipped zero-entry INTENT.md template (the real template file
    projects seed from) parses to an empty entry list.
  - A file with several dated entries parses each one with its date and
    declaration text intact, in file order.
  - An absent file, and an unreadable file, both yield an empty list and
    raise nothing.
  - A malformed entry (missing/invalid date, or a body that spills onto a
    continuation line instead of terminating on its own line) is dropped
    whole; well-formed entries elsewhere in the same file still parse.
  - Text inside a fenced code block is never read as a live entry, and
    text above the "## Entries" heading is never read as a live entry —
    together these are what keep the template's own worked example inert.
  - The reader performs no writes: parsing against a read-only fixture
    directory leaves the directory listing and the file's content
    byte-identical.
  - intent_path_for_project()/read_project_intent() resolve and parse the
    standard <kanban_root>/projects/<project>/INTENT.md layout.

All tests use tmp_path; no live filesystem paths. Function names describe
behaviour under test, never bug/task IDs (roles/TESTER.md "Test Authoring
Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import stat

import pytest

from pgai_agent_kanban.overwatch.intent import (
    IntentEntry,
    intent_path_for_project,
    parse_entries,
    read_project_intent,
)

# team/tests/unit/test_intent.py -> team/
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_SHIPPED_TEMPLATE = _TEAM_DIR / "templates" / "project" / "release" / "INTENT-TEMPLATE.md"


def _write_intent(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
    path = tmp_path / "INTENT.md"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Absent / unreadable file
# ---------------------------------------------------------------------------


def test_absent_file_returns_empty_list_and_raises_nothing(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "does-not-exist" / "INTENT.md"
    assert parse_entries(str(missing)) == []


def test_unreadable_file_returns_empty_list_and_raises_nothing(tmp_path: pathlib.Path) -> None:
    path = _write_intent(
        tmp_path,
        "## Entries\n\n- 2026-01-01: an entry a permission-denied read must never surface\n",
    )
    original_mode = path.stat().st_mode
    try:
        path.chmod(0o000)
        if os.access(str(path), os.R_OK):
            pytest.skip("process can read despite mode 000 (likely running as root)")
        assert parse_entries(str(path)) == []
    finally:
        path.chmod(original_mode)


# ---------------------------------------------------------------------------
# Shipped template
# ---------------------------------------------------------------------------


def test_shipped_template_parses_to_empty_entry_list() -> None:
    assert _SHIPPED_TEMPLATE.is_file(), f"Expected shipped template at {_SHIPPED_TEMPLATE}"
    assert parse_entries(str(_SHIPPED_TEMPLATE)) == []


# ---------------------------------------------------------------------------
# Well-formed entries
# ---------------------------------------------------------------------------


def test_single_entry_parses_with_date_and_declaration_intact(tmp_path: pathlib.Path) -> None:
    path = _write_intent(
        tmp_path,
        "## Entries\n\n"
        "- 2026-03-14: ai_main rebased to v2.4.0 - hindsight experiment; "
        "lineage on last_run_ai_main\n",
    )
    entries = parse_entries(str(path))
    assert entries == [
        IntentEntry(
            date="2026-03-14",
            declaration="ai_main rebased to v2.4.0 - hindsight experiment; "
            "lineage on last_run_ai_main",
        )
    ]


def test_three_entries_parse_in_file_order_each_with_date_and_text(
    tmp_path: pathlib.Path,
) -> None:
    path = _write_intent(
        tmp_path,
        "## Entries\n\n"
        "- 2026-01-05: first deliberate state, explained here\n"
        "- 2026-02-18: second deliberate state, explained here\n"
        "- 2026-03-30: third deliberate state, explained here\n",
    )
    entries = parse_entries(str(path))
    assert entries == [
        IntentEntry(date="2026-01-05", declaration="first deliberate state, explained here"),
        IntentEntry(date="2026-02-18", declaration="second deliberate state, explained here"),
        IntentEntry(date="2026-03-30", declaration="third deliberate state, explained here"),
    ]


# ---------------------------------------------------------------------------
# Malformed shapes — dropped whole, neighbors unaffected
# ---------------------------------------------------------------------------


def test_entry_missing_or_invalid_date_is_dropped_but_neighbors_still_parse(
    tmp_path: pathlib.Path,
) -> None:
    path = _write_intent(
        tmp_path,
        "## Entries\n\n"
        "- 2026-01-01: well-formed entry before the bad ones\n"
        "- no date at all: this line never names a real date\n"
        "- 2026-13-40: this date shape is not a real calendar day\n"
        "- 2026-12-25: well-formed entry after the bad ones\n",
    )
    entries = parse_entries(str(path))
    assert entries == [
        IntentEntry(date="2026-01-01", declaration="well-formed entry before the bad ones"),
        IntentEntry(date="2026-12-25", declaration="well-formed entry after the bad ones"),
    ]


def test_entry_body_spilling_onto_a_continuation_line_is_dropped_whole(
    tmp_path: pathlib.Path,
) -> None:
    path = _write_intent(
        tmp_path,
        "## Entries\n\n"
        "- 2026-01-01: well-formed entry before the wrapped one\n"
        "- 2026-02-02: an operator wrapped this long declaration onto\n"
        "  a second physical line instead of keeping it on one line\n"
        "- 2026-03-03: well-formed entry after the wrapped one\n",
    )
    entries = parse_entries(str(path))
    # The wrapped entry is dropped whole -- never truncated to just its
    # first line, which would silently misrepresent what the operator wrote.
    assert entries == [
        IntentEntry(date="2026-01-01", declaration="well-formed entry before the wrapped one"),
        IntentEntry(date="2026-03-03", declaration="well-formed entry after the wrapped one"),
    ]
    assert not any("wrapped" in e.declaration and e.date == "2026-02-02" for e in entries)


# ---------------------------------------------------------------------------
# Fenced examples and pre-heading text are never live entries
# ---------------------------------------------------------------------------


def test_fenced_worked_example_inside_entries_section_is_ignored(
    tmp_path: pathlib.Path,
) -> None:
    path = _write_intent(
        tmp_path,
        "## Entries\n\n"
        "```\n"
        "- 2026-01-01: EXAMPLE ONLY -- this is fenced and must never parse\n"
        "```\n\n"
        "- 2026-06-06: the only real entry, outside the fence\n",
    )
    entries = parse_entries(str(path))
    assert entries == [IntentEntry(date="2026-06-06", declaration="the only real entry, outside the fence")]


def test_bullet_lines_before_the_entries_heading_are_ignored(
    tmp_path: pathlib.Path,
) -> None:
    path = _write_intent(
        tmp_path,
        "## Worked example (NOT a live entry)\n\n"
        "- 2026-01-01: this bullet sits before the Entries heading\n\n"
        "## Entries\n\n"
        "- 2026-07-07: this is the only real entry\n",
    )
    entries = parse_entries(str(path))
    assert entries == [IntentEntry(date="2026-07-07", declaration="this is the only real entry")]


def test_file_with_no_entries_heading_parses_to_empty_list(tmp_path: pathlib.Path) -> None:
    path = _write_intent(
        tmp_path,
        "# INTENT.md\n\nThis file has no Entries heading at all.\n"
        "- 2026-01-01: this bullet is never under a recognised section\n",
    )
    assert parse_entries(str(path)) == []


# ---------------------------------------------------------------------------
# Read-only: the reader performs no writes
# ---------------------------------------------------------------------------


def test_parser_performs_no_writes_against_a_read_only_fixture_directory(
    tmp_path: pathlib.Path,
) -> None:
    fixture_dir = tmp_path / "read_only_project"
    fixture_dir.mkdir()
    path = _write_intent(
        fixture_dir,
        "## Entries\n\n- 2026-08-08: entry read from a locked-down directory\n",
    )
    before_listing = sorted(os.listdir(fixture_dir))
    before_content = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns

    dir_mode = fixture_dir.stat().st_mode
    try:
        # No write, execute-only: still readable, but nothing can be
        # created, deleted, or renamed inside the directory.
        fixture_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
        entries = parse_entries(str(path))
    finally:
        fixture_dir.chmod(dir_mode)

    assert entries == [
        IntentEntry(date="2026-08-08", declaration="entry read from a locked-down directory")
    ]
    assert sorted(os.listdir(fixture_dir)) == before_listing
    assert path.read_bytes() == before_content
    assert path.stat().st_mtime_ns == before_mtime


# ---------------------------------------------------------------------------
# Project-scoped path resolution
# ---------------------------------------------------------------------------


def test_intent_path_for_project_returns_standard_layout(tmp_path: pathlib.Path) -> None:
    expected = str(tmp_path / "projects" / "demo-project" / "INTENT.md")
    assert intent_path_for_project(str(tmp_path), "demo-project") == expected


def test_read_project_intent_resolves_standard_path_and_parses(
    tmp_path: pathlib.Path,
) -> None:
    project_dir = tmp_path / "projects" / "demo-project"
    project_dir.mkdir(parents=True)
    _write_intent(
        project_dir,
        "## Entries\n\n- 2026-09-09: entry reached through the project-scoped reader\n",
    )
    entries = read_project_intent(str(tmp_path), "demo-project")
    assert entries == [
        IntentEntry(date="2026-09-09", declaration="entry reached through the project-scoped reader")
    ]


def test_read_project_intent_returns_empty_list_for_a_project_with_no_intent_file(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "projects" / "bare-project").mkdir(parents=True)
    assert read_project_intent(str(tmp_path), "bare-project") == []
