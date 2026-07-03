"""
test_pm_status.py
=================
Behavioral unit tests for team/pm-agent/pm_status.py.

Covers:
  - get_status_fields(): parse status.md into a heading -> body dict.
  - is_queue_entry(): identify lines that look like queue entries.
  - extract_task_id_from_queue_line(): extract (task_id, is_done) from a queue line.
  - scan_queue_files(): scan a directory of per-agent backlog files.
  - check_desync(): detect mismatches between queue markers and status.md states.

All filesystem interactions use tmp_path.  No subprocess calls, no live
kanban tree access, no bare /tmp paths.

Test function names describe the behavior under test (SOP.md anti-pattern 6).
"""

from __future__ import annotations

import pathlib

import pytest

# ---------------------------------------------------------------------------
# Import with installed-package / direct-checkout fallback
# ---------------------------------------------------------------------------
try:
    import pm_agent.pm_status as pm_status_mod
    from pm_agent.pm_status import (
        get_status_fields,
        is_queue_entry,
        extract_task_id_from_queue_line,
        scan_queue_files,
        check_desync,
    )
except ImportError:
    import sys
    sys.path.insert(
        0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent")
    )
    import pm_status as pm_status_mod  # type: ignore[no-redef]
    from pm_status import (  # type: ignore[no-redef]
        get_status_fields,
        is_queue_entry,
        extract_task_id_from_queue_line,
        scan_queue_files,
        check_desync,
    )


# ---------------------------------------------------------------------------
# Helpers: build fixture files
# ---------------------------------------------------------------------------

def _write_status(directory: pathlib.Path, content: str) -> pathlib.Path:
    """Write a status.md file into directory and return its path."""
    p = directory / "status.md"
    p.write_text(content, encoding="utf-8")
    return p


def _write_backlog(queues_dir: pathlib.Path, agent: str, content: str) -> pathlib.Path:
    """Write a <agent>_backlog.md file inside queues_dir and return its path."""
    queues_dir.mkdir(parents=True, exist_ok=True)
    p = queues_dir / f"{agent}_backlog.md"
    p.write_text(content, encoding="utf-8")
    return p


def _make_task_status(
    tasks_dir: pathlib.Path, task_id: str, state: str
) -> pathlib.Path:
    """Create a tasks/<task_id>/status.md with the given State value."""
    task_dir = tasks_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    content = f"# Status\n\n## State\n{state}\n"
    return _write_status(task_dir, content)


# ---------------------------------------------------------------------------
# get_status_fields() — parse heading -> body dict
# ---------------------------------------------------------------------------


def test_get_status_fields_parses_single_heading(tmp_path: pathlib.Path) -> None:
    """get_status_fields returns a dict with one key for a one-section file."""
    status_file = _write_status(
        tmp_path,
        "## State\nWORKING\n",
    )
    result = get_status_fields(status_file)
    assert result.get("State") == "WORKING"


def test_get_status_fields_parses_multiple_headings(tmp_path: pathlib.Path) -> None:
    """get_status_fields parses all ## sections into a single dict."""
    content = (
        "## State\nDONE\n\n"
        "## Summary\nWork completed successfully.\n\n"
        "## Blockers\nnone\n"
    )
    status_file = _write_status(tmp_path, content)
    result = get_status_fields(status_file)
    assert result.get("State") == "DONE"
    assert "Work completed successfully." in result.get("Summary", "")
    assert result.get("Blockers") == "none"


def test_get_status_fields_strips_trailing_whitespace_from_body(
    tmp_path: pathlib.Path,
) -> None:
    """get_status_fields strips leading/trailing whitespace from each section body."""
    content = "## State\n\n   WORKING   \n\n## Summary\n  Some text.  \n"
    status_file = _write_status(tmp_path, content)
    result = get_status_fields(status_file)
    # strip() is called on the joined body; internal leading/trailing is trimmed.
    assert result["State"] == "WORKING"


def test_get_status_fields_handles_multiline_body(tmp_path: pathlib.Path) -> None:
    """get_status_fields captures multi-line section bodies as a single string."""
    content = "## Summary\nLine one.\nLine two.\nLine three.\n"
    status_file = _write_status(tmp_path, content)
    result = get_status_fields(status_file)
    assert "Line one." in result["Summary"]
    assert "Line two." in result["Summary"]
    assert "Line three." in result["Summary"]


def test_get_status_fields_returns_empty_dict_for_empty_file(
    tmp_path: pathlib.Path,
) -> None:
    """get_status_fields returns {} for a file with no ## headings."""
    status_file = _write_status(tmp_path, "no headings here\n")
    result = get_status_fields(status_file)
    assert result == {}


def test_get_status_fields_returns_empty_dict_for_blank_file(
    tmp_path: pathlib.Path,
) -> None:
    """get_status_fields returns {} for a completely blank file."""
    status_file = _write_status(tmp_path, "")
    result = get_status_fields(status_file)
    assert result == {}


def test_get_status_fields_ignores_h1_headings(tmp_path: pathlib.Path) -> None:
    """get_status_fields ignores single-hash (h1) headings; only ## sections matter."""
    content = "# Title\n\n## State\nBACKLOG\n"
    status_file = _write_status(tmp_path, content)
    result = get_status_fields(status_file)
    assert "Title" not in result
    assert result.get("State") == "BACKLOG"


def test_get_status_fields_handles_typical_coder_status(tmp_path: pathlib.Path) -> None:
    """get_status_fields handles a realistic CODER status.md structure."""
    content = (
        "# Status\n\n"
        "## Task\nCODER-20260101-001-my-task\n\n"
        "## State\nDONE\n\n"
        "## Blockers\nnone\n\n"
        "## Needs Human\nno\n\n"
        "## Next Recommended Step\nWork merged to rc/v0.1.0.\n"
    )
    status_file = _write_status(tmp_path, content)
    result = get_status_fields(status_file)
    assert result.get("State") == "DONE"
    assert result.get("Needs Human") == "no"
    assert result.get("Blockers") == "none"
    assert "merged" in result.get("Next Recommended Step", "")


# ---------------------------------------------------------------------------
# is_queue_entry() — identify queue entry lines
# ---------------------------------------------------------------------------


def test_is_queue_entry_recognizes_unchecked_dash_format(
) -> None:
    """is_queue_entry returns True for '- [ ] TASK-ID' format."""
    assert is_queue_entry("- [ ] CODER-20260101-001-my-task")


def test_is_queue_entry_recognizes_checked_dash_format() -> None:
    """is_queue_entry returns True for '- [x] TASK-ID' format."""
    assert is_queue_entry("- [x] CODER-20260101-001-my-task")


def test_is_queue_entry_recognizes_asterisk_prefix() -> None:
    """is_queue_entry returns True for '* [ ] TASK-ID' format."""
    assert is_queue_entry("* [ ] WRITER-20260101-002-write-docs")


def test_is_queue_entry_accepts_leading_whitespace() -> None:
    """is_queue_entry returns True when the line has leading spaces."""
    assert is_queue_entry("  - [ ] PM-20260101-003-my-task")


def test_is_queue_entry_rejects_plain_text_line() -> None:
    """is_queue_entry returns False for a plain prose line."""
    assert not is_queue_entry("This is just a heading or note.")


def test_is_queue_entry_rejects_blank_line() -> None:
    """is_queue_entry returns False for an empty string."""
    assert not is_queue_entry("")


def test_is_queue_entry_rejects_h2_heading() -> None:
    """is_queue_entry returns False for a markdown ## heading line."""
    assert not is_queue_entry("## CODER queue")


def test_is_queue_entry_with_trailing_text() -> None:
    """is_queue_entry returns True when the line has optional trailing text."""
    assert is_queue_entry("- [ ] CODER-20260101-001-my-task  # optional note")


# ---------------------------------------------------------------------------
# extract_task_id_from_queue_line() — parse (task_id, is_done)
# ---------------------------------------------------------------------------


def test_extract_task_id_returns_id_and_not_done_for_unchecked(
) -> None:
    """extract_task_id_from_queue_line returns (task_id, False) for '[ ]' marker."""
    result = extract_task_id_from_queue_line("- [ ] CODER-20260101-001-slug")
    assert result is not None
    task_id, is_done = result
    assert task_id == "CODER-20260101-001-slug"
    assert is_done is False


def test_extract_task_id_returns_id_and_done_for_x_marker() -> None:
    """extract_task_id_from_queue_line returns (task_id, True) for '[x]' marker."""
    result = extract_task_id_from_queue_line("- [x] CODER-20260101-001-slug")
    assert result is not None
    task_id, is_done = result
    assert task_id == "CODER-20260101-001-slug"
    assert is_done is True


def test_extract_task_id_is_case_insensitive_for_x_marker() -> None:
    """extract_task_id_from_queue_line treats '[X]' (uppercase) as done."""
    result = extract_task_id_from_queue_line("- [X] TESTER-20260101-002-verify")
    assert result is not None
    _, is_done = result
    assert is_done is True


def test_extract_task_id_handles_asterisk_prefix() -> None:
    """extract_task_id_from_queue_line works with '* [ ] TASK-ID' format."""
    result = extract_task_id_from_queue_line("* [ ] PM-20260101-003-make-plan")
    assert result is not None
    task_id, is_done = result
    assert task_id == "PM-20260101-003-make-plan"
    assert is_done is False


def test_extract_task_id_returns_none_for_non_queue_line() -> None:
    """extract_task_id_from_queue_line returns None for a plain prose line."""
    result = extract_task_id_from_queue_line("Just a heading or note.")
    assert result is None


def test_extract_task_id_returns_none_for_blank_line() -> None:
    """extract_task_id_from_queue_line returns None for an empty string."""
    result = extract_task_id_from_queue_line("")
    assert result is None


def test_extract_task_id_handles_leading_whitespace() -> None:
    """extract_task_id_from_queue_line handles leading whitespace correctly."""
    result = extract_task_id_from_queue_line("  - [ ] CODER-20260101-001-slug")
    assert result is not None
    task_id, _ = result
    assert task_id == "CODER-20260101-001-slug"


def test_extract_task_id_ignores_trailing_text() -> None:
    """extract_task_id_from_queue_line returns only the task ID, ignoring trailing text."""
    result = extract_task_id_from_queue_line("- [ ] CODER-20260101-001-slug  extra text")
    assert result is not None
    task_id, _ = result
    assert task_id == "CODER-20260101-001-slug"


# ---------------------------------------------------------------------------
# scan_queue_files() — scan a queues/ directory
# ---------------------------------------------------------------------------


def test_scan_queue_files_returns_entries_from_backlog_file(
    tmp_path: pathlib.Path,
) -> None:
    """scan_queue_files returns a list with entries from a *_backlog.md file."""
    tasks_dir = tmp_path / "tasks"
    queues_dir = tasks_dir / "queues"
    _write_backlog(
        queues_dir,
        "coder",
        "# Coder Backlog\n- [ ] CODER-20260101-001-slug\n- [x] CODER-20260101-002-done\n",
    )

    results = scan_queue_files(tasks_dir)

    assert len(results) == 2
    task_lines = [r["task_line"] for r in results]
    assert any("CODER-20260101-001-slug" in tl for tl in task_lines)
    assert any("CODER-20260101-002-done" in tl for tl in task_lines)


def test_scan_queue_files_returns_queue_name_with_each_entry(
    tmp_path: pathlib.Path,
) -> None:
    """scan_queue_files includes the queue file stem in each result dict."""
    tasks_dir = tmp_path / "tasks"
    queues_dir = tasks_dir / "queues"
    _write_backlog(
        queues_dir,
        "writer",
        "- [ ] WRITER-20260101-001-docs\n",
    )

    results = scan_queue_files(tasks_dir)

    assert len(results) == 1
    assert results[0]["queue"] == "writer_backlog"


def test_scan_queue_files_returns_empty_list_when_no_queues_dir(
    tmp_path: pathlib.Path,
) -> None:
    """scan_queue_files returns [] when tasks/queues/ directory does not exist."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    results = scan_queue_files(tasks_dir)
    assert results == []


def test_scan_queue_files_skips_non_queue_entry_lines(
    tmp_path: pathlib.Path,
) -> None:
    """scan_queue_files skips headings and prose lines in backlog files."""
    tasks_dir = tmp_path / "tasks"
    queues_dir = tasks_dir / "queues"
    _write_backlog(
        queues_dir,
        "coder",
        "# Coder Backlog\n\nSome intro text.\n\n- [ ] CODER-20260101-001-slug\n",
    )

    results = scan_queue_files(tasks_dir)

    assert len(results) == 1
    assert "CODER-20260101-001-slug" in results[0]["task_line"]


def test_scan_queue_files_handles_multiple_backlog_files(
    tmp_path: pathlib.Path,
) -> None:
    """scan_queue_files aggregates entries from all *_backlog.md files."""
    tasks_dir = tmp_path / "tasks"
    queues_dir = tasks_dir / "queues"
    _write_backlog(queues_dir, "coder", "- [ ] CODER-20260101-001-a\n")
    _write_backlog(queues_dir, "writer", "- [ ] WRITER-20260101-001-b\n")
    _write_backlog(queues_dir, "tester", "- [ ] TESTER-20260101-001-c\n")

    results = scan_queue_files(tasks_dir)

    assert len(results) == 3
    task_ids_found = {r["task_line"].split()[-1] for r in results}
    assert "CODER-20260101-001-a" in task_ids_found
    assert "WRITER-20260101-001-b" in task_ids_found
    assert "TESTER-20260101-001-c" in task_ids_found


def test_scan_queue_files_returns_empty_list_for_empty_backlog_file(
    tmp_path: pathlib.Path,
) -> None:
    """scan_queue_files returns [] when the backlog file exists but has no entries."""
    tasks_dir = tmp_path / "tasks"
    queues_dir = tasks_dir / "queues"
    _write_backlog(queues_dir, "coder", "# Coder Backlog\n\nNo tasks yet.\n")

    results = scan_queue_files(tasks_dir)

    assert results == []


# ---------------------------------------------------------------------------
# check_desync() — detect queue marker vs status.md state mismatches
# ---------------------------------------------------------------------------


def test_check_desync_returns_empty_when_all_in_sync(
    tmp_path: pathlib.Path,
) -> None:
    """check_desync returns [] when all queue markers match status.md states."""
    tasks_dir = tmp_path / "tasks"
    queues_dir = tasks_dir / "queues"
    team_root = str(tmp_path)

    _make_task_status(tasks_dir, "CODER-20260101-001-active", "WORKING")
    _make_task_status(tasks_dir, "CODER-20260101-002-done", "DONE")
    _write_backlog(
        queues_dir,
        "coder",
        "- [ ] CODER-20260101-001-active\n- [x] CODER-20260101-002-done\n",
    )

    desyncs = check_desync(tasks_dir, team_root)
    assert desyncs == []


def test_check_desync_detects_checked_marker_for_non_done_task(
    tmp_path: pathlib.Path,
) -> None:
    """check_desync flags a task marked [x] whose status.md state is not DONE."""
    tasks_dir = tmp_path / "tasks"
    queues_dir = tasks_dir / "queues"
    team_root = str(tmp_path)

    _make_task_status(tasks_dir, "CODER-20260101-001-working", "WORKING")
    _write_backlog(
        queues_dir,
        "coder",
        "- [x] CODER-20260101-001-working\n",
    )

    desyncs = check_desync(tasks_dir, team_root)
    assert len(desyncs) == 1
    queue_name, task_id, marker_str, state = desyncs[0]
    assert task_id == "CODER-20260101-001-working"
    assert marker_str == "[x]"
    assert state == "WORKING"


def test_check_desync_detects_unchecked_marker_for_done_task(
    tmp_path: pathlib.Path,
) -> None:
    """check_desync flags a task marked [ ] whose status.md state is DONE."""
    tasks_dir = tmp_path / "tasks"
    queues_dir = tasks_dir / "queues"
    team_root = str(tmp_path)

    _make_task_status(tasks_dir, "WRITER-20260101-001-docs", "DONE")
    _write_backlog(
        queues_dir,
        "writer",
        "- [ ] WRITER-20260101-001-docs\n",
    )

    desyncs = check_desync(tasks_dir, team_root)
    assert len(desyncs) == 1
    queue_name, task_id, marker_str, state = desyncs[0]
    assert task_id == "WRITER-20260101-001-docs"
    assert marker_str == "[ ]"
    assert state == "DONE"


def test_check_desync_treats_wont_do_as_done_state(
    tmp_path: pathlib.Path,
) -> None:
    """check_desync does not flag WONT-DO tasks when marker is [x]."""
    tasks_dir = tmp_path / "tasks"
    queues_dir = tasks_dir / "queues"
    team_root = str(tmp_path)

    _make_task_status(tasks_dir, "CODER-20260101-001-cancelled", "WONT-DO")
    _write_backlog(
        queues_dir,
        "coder",
        "- [x] CODER-20260101-001-cancelled\n",
    )

    desyncs = check_desync(tasks_dir, team_root)
    assert desyncs == []


def test_check_desync_skips_tasks_without_status_file(
    tmp_path: pathlib.Path,
) -> None:
    """check_desync silently skips queue entries that lack a status.md file."""
    tasks_dir = tmp_path / "tasks"
    queues_dir = tasks_dir / "queues"
    team_root = str(tmp_path)

    # Note: we do NOT create a status.md for this task
    tasks_dir.mkdir(parents=True, exist_ok=True)
    _write_backlog(
        queues_dir,
        "coder",
        "- [ ] CODER-20260101-001-missing-status\n",
    )

    desyncs = check_desync(tasks_dir, team_root)
    assert desyncs == []


def test_check_desync_returns_empty_when_no_queues_dir(
    tmp_path: pathlib.Path,
) -> None:
    """check_desync returns [] when the queues/ directory does not exist."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    team_root = str(tmp_path)

    desyncs = check_desync(tasks_dir, team_root)
    assert desyncs == []


def test_check_desync_handles_multiple_desyncs_across_queues(
    tmp_path: pathlib.Path,
) -> None:
    """check_desync reports all desyncs found across multiple queue files."""
    tasks_dir = tmp_path / "tasks"
    queues_dir = tasks_dir / "queues"
    team_root = str(tmp_path)

    # Task 1: marked done but state is WORKING (desync)
    _make_task_status(tasks_dir, "CODER-20260101-001-active", "WORKING")
    _write_backlog(
        queues_dir,
        "coder",
        "- [x] CODER-20260101-001-active\n",
    )
    # Task 2: marked undone but state is DONE (desync)
    _make_task_status(tasks_dir, "WRITER-20260101-001-done", "DONE")
    _write_backlog(
        queues_dir,
        "writer",
        "- [ ] WRITER-20260101-001-done\n",
    )

    desyncs = check_desync(tasks_dir, team_root)
    assert len(desyncs) == 2
    task_ids = {d[1] for d in desyncs}
    assert "CODER-20260101-001-active" in task_ids
    assert "WRITER-20260101-001-done" in task_ids


def test_check_desync_reports_queue_name_in_each_desync(
    tmp_path: pathlib.Path,
) -> None:
    """check_desync includes the queue file name in each desync tuple."""
    tasks_dir = tmp_path / "tasks"
    queues_dir = tasks_dir / "queues"
    team_root = str(tmp_path)

    _make_task_status(tasks_dir, "CODER-20260101-001-bad", "WORKING")
    _write_backlog(
        queues_dir,
        "coder",
        "- [x] CODER-20260101-001-bad\n",
    )

    desyncs = check_desync(tasks_dir, team_root)
    assert len(desyncs) == 1
    queue_name = desyncs[0][0]
    assert queue_name == "coder_backlog"


def test_check_desync_ignores_blocked_and_waiting_as_not_done(
    tmp_path: pathlib.Path,
) -> None:
    """check_desync treats BLOCKED and WAITING as non-done states (marker [ ] is correct)."""
    tasks_dir = tmp_path / "tasks"
    queues_dir = tasks_dir / "queues"
    team_root = str(tmp_path)

    _make_task_status(tasks_dir, "CODER-20260101-001-blocked", "BLOCKED")
    _make_task_status(tasks_dir, "CODER-20260101-002-waiting", "WAITING")
    _write_backlog(
        queues_dir,
        "coder",
        "- [ ] CODER-20260101-001-blocked\n- [ ] CODER-20260101-002-waiting\n",
    )

    desyncs = check_desync(tasks_dir, team_root)
    assert desyncs == []
