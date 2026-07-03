"""
test_reports.py
===============
Behavioral unit tests for team/pgai_agent_kanban/reports/:
  - reports/project_summary.py  (priority target — multiple branch coverage)
  - reports/agent_timing.py

All filesystem access uses tmp_path; clocks are patched where needed.
No integration seams (LLM API, real kanban root) are exercised.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# agent_timing imports
# ---------------------------------------------------------------------------

from pgai_agent_kanban.reports.agent_timing import (
    WakeEntry,
    _parse_timestamp,
    parse_log_file,
    collect_log_files,
    aggregate,
    RoleProjectKey,
    render_table,
    resolve_logs_dir,
)

# ---------------------------------------------------------------------------
# project_summary imports
# ---------------------------------------------------------------------------

from pgai_agent_kanban.reports.project_summary import (
    _kanban_root,
    _list_projects,
    _extract_field,
    _extract_bold_field,
    _normalize_severity,
    _parse_date_loose,
    _date_from_task_id,
    _slug_from_filename,
    _truncate,
    _read_ini_value,
    _safe_read,
    BugItem,
    PriorityItem,
    RequirementItem,
    ReleaseNote,
    TaskEvent,
    ProjectReport,
    apply_offline_summaries,
    render_project_report,
    render_aggregate,
    _render_json,
    _load_bugs,
    _load_priorities,
    _load_requirements,
    _load_release_notes,
    _load_task_events,
    _offline_oneliner_bug,
    _offline_oneliner_priority,
    _offline_oneliner_requirement,
    _offline_oneliner_release,
    _fmt_date,
    _hr,
    _section_header,
    _bullet,
    _sort_key_date,
)


# ===========================================================================
# agent_timing — _parse_timestamp
# ===========================================================================


def test_parse_timestamp_iso8601_with_offset_returns_aware_datetime() -> None:
    """_parse_timestamp parses ISO 8601 with +00:00 offset into an aware datetime."""
    dt = _parse_timestamp("2026-05-24T18:03:44+00:00")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 5
    assert dt.day == 24
    assert dt.tzinfo is not None


def test_parse_timestamp_z_suffix_normalised_to_utc() -> None:
    """_parse_timestamp handles a Z suffix by normalising it to +00:00."""
    dt = _parse_timestamp("2026-06-01T12:00:00Z")
    assert dt is not None
    assert dt.year == 2026
    assert dt.hour == 12


def test_parse_timestamp_malformed_string_returns_none() -> None:
    """_parse_timestamp returns None for an unparseable string."""
    assert _parse_timestamp("not-a-timestamp") is None


def test_parse_timestamp_empty_string_returns_none() -> None:
    """_parse_timestamp returns None for an empty string."""
    assert _parse_timestamp("") is None


# ===========================================================================
# agent_timing — parse_log_file
# ===========================================================================


def test_parse_log_file_extracts_wake_done_entries(tmp_path: pathlib.Path) -> None:
    """parse_log_file returns WakeEntry records for matching wake-done lines."""
    log = tmp_path / "batch.log"
    log.write_text(
        "[2026-05-24T18:03:44+00:00] wake(cm): project pgai-agent-kanban:"
        " done: completed=1 elapsed=103s reason=\"ok\"\n"
        "[2026-05-24T18:05:00+00:00] wake(coder): project pgai-agent-kanban:"
        " done: completed=2 elapsed=42s reason=\"ok\"\n",
        encoding="utf-8",
    )
    entries = parse_log_file(log, cutoff=None)
    assert len(entries) == 2
    assert entries[0].agent == "cm"
    assert entries[0].elapsed_seconds == 103
    assert entries[1].agent == "coder"
    assert entries[1].elapsed_seconds == 42


def test_parse_log_file_skips_lines_before_cutoff(tmp_path: pathlib.Path) -> None:
    """parse_log_file excludes entries whose timestamp predates the cutoff."""
    log = tmp_path / "batch.log"
    log.write_text(
        "[2026-01-01T00:00:00+00:00] wake(cm): project pgai-agent-kanban:"
        " done: completed=1 elapsed=10s reason=\"old\"\n"
        "[2026-06-01T00:00:00+00:00] wake(cm): project pgai-agent-kanban:"
        " done: completed=1 elapsed=20s reason=\"new\"\n",
        encoding="utf-8",
    )
    cutoff = datetime(2026, 3, 1, tzinfo=timezone.utc)
    entries = parse_log_file(log, cutoff=cutoff)
    assert len(entries) == 1
    assert entries[0].elapsed_seconds == 20


def test_parse_log_file_skips_non_matching_lines(tmp_path: pathlib.Path) -> None:
    """parse_log_file ignores lines that do not match the wake-done pattern."""
    log = tmp_path / "batch.log"
    log.write_text(
        "INFO: starting agent\n"
        "DEBUG: doing something\n"
        "[2026-05-24T18:03:44+00:00] wake(pm): project proj:"
        " done: completed=1 elapsed=55s reason=\"ok\"\n",
        encoding="utf-8",
    )
    entries = parse_log_file(log, cutoff=None)
    assert len(entries) == 1
    assert entries[0].agent == "pm"


def test_parse_log_file_returns_empty_for_missing_file(tmp_path: pathlib.Path) -> None:
    """parse_log_file returns an empty list when the file does not exist."""
    entries = parse_log_file(tmp_path / "nonexistent.log", cutoff=None)
    assert entries == []


# ===========================================================================
# agent_timing — collect_log_files
# ===========================================================================


def test_collect_log_files_finds_batch_logs_under_agents_subdir(
    tmp_path: pathlib.Path,
) -> None:
    """collect_log_files returns log files matching *-batch-*.log under logs/agents/."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "cm-batch-20260601.log").write_text("data", encoding="utf-8")
    (agents_dir / "coder-batch-20260601.log").write_text("data", encoding="utf-8")
    (agents_dir / "README.txt").write_text("ignored", encoding="utf-8")

    files = collect_log_files(tmp_path)
    names = [f.name for f in files]
    assert "cm-batch-20260601.log" in names
    assert "coder-batch-20260601.log" in names
    assert "README.txt" not in names


def test_collect_log_files_returns_empty_for_nonexistent_dir(
    tmp_path: pathlib.Path,
) -> None:
    """collect_log_files returns an empty list when no valid directory exists."""
    result = collect_log_files(tmp_path / "missing")
    assert result == []


# ===========================================================================
# agent_timing — aggregate
# ===========================================================================


def test_aggregate_groups_by_agent_and_project() -> None:
    """aggregate returns one bucket per (agent, project) key with elapsed values."""
    entries = [
        WakeEntry(ts=datetime(2026, 6, 1, tzinfo=timezone.utc), agent="cm", project="proj-a", elapsed_seconds=100),
        WakeEntry(ts=datetime(2026, 6, 1, tzinfo=timezone.utc), agent="cm", project="proj-a", elapsed_seconds=200),
        WakeEntry(ts=datetime(2026, 6, 1, tzinfo=timezone.utc), agent="coder", project="proj-a", elapsed_seconds=50),
    ]
    buckets = aggregate(entries)
    key_cm = RoleProjectKey(agent="cm", project="proj-a")
    key_coder = RoleProjectKey(agent="coder", project="proj-a")
    assert key_cm in buckets
    assert buckets[key_cm] == [100, 200]
    assert key_coder in buckets
    assert buckets[key_coder] == [50]


def test_aggregate_empty_entries_returns_empty_dict() -> None:
    """aggregate returns an empty dict when no entries are provided."""
    assert aggregate([]) == {}


# ===========================================================================
# agent_timing — render_table
# ===========================================================================


def test_render_table_contains_header_columns() -> None:
    """render_table output includes the column headers agent_role and project."""
    buckets = {
        RoleProjectKey(agent="cm", project="pgai-agent-kanban"): [100, 200],
    }
    output = render_table(buckets, window_label="last 7 days")
    assert "agent_role" in output
    assert "project" in output
    assert "avg_seconds" in output


def test_render_table_computes_correct_average() -> None:
    """render_table displays the correct average elapsed seconds."""
    buckets = {
        RoleProjectKey(agent="cm", project="proj"): [100, 300],
    }
    output = render_table(buckets, window_label="test window")
    # avg = (100 + 300) / 2 = 200.0
    assert "200.0" in output


def test_render_table_empty_buckets_shows_no_data_message() -> None:
    """render_table shows a 'no wake log data' message when buckets is empty."""
    output = render_table({}, window_label="last 7 days")
    assert "no wake log data" in output


def test_render_table_includes_window_label() -> None:
    """render_table includes the window_label string in the output."""
    output = render_table({}, window_label="all time")
    assert "all time" in output


# ===========================================================================
# agent_timing — resolve_logs_dir
# ===========================================================================


def test_resolve_logs_dir_uses_env_var(tmp_path: pathlib.Path) -> None:
    """resolve_logs_dir returns <PGAI_AGENT_KANBAN_ROOT_PATH>/logs/ when env var set."""
    with patch.dict("os.environ", {"PGAI_AGENT_KANBAN_ROOT_PATH": str(tmp_path)}):
        result = resolve_logs_dir()
    assert result == tmp_path / "logs"


# ===========================================================================
# project_summary — _extract_field
# ===========================================================================


def test_extract_field_returns_value_after_heading() -> None:
    """_extract_field returns the first non-blank line after a ## heading."""
    text = "## Status\nopen\n\n## Other\nfoo\n"
    assert _extract_field(text, "Status") == "open"


def test_extract_field_returns_empty_when_heading_absent() -> None:
    """_extract_field returns '' when the requested heading is not present."""
    text = "## Title\nSomething\n"
    assert _extract_field(text, "Status") == ""


def test_extract_field_is_case_insensitive() -> None:
    """_extract_field matches headings case-insensitively."""
    text = "## STATUS\nhigh\n"
    assert _extract_field(text, "status") == "high"


def test_extract_field_skips_blank_lines_between_heading_and_value() -> None:
    """_extract_field skips blank lines before the first content line."""
    text = "## Severity\n\nmedium\n"
    assert _extract_field(text, "Severity") == "medium"


def test_extract_field_stops_at_next_heading() -> None:
    """_extract_field does not include content from subsequent sections."""
    text = "## Severity\nhigh\n## Status\nopen\n"
    result = _extract_field(text, "Severity")
    assert result == "high"


# ===========================================================================
# project_summary — _extract_bold_field
# ===========================================================================


def test_extract_bold_field_handles_colon_inside_markers() -> None:
    """_extract_bold_field parses **Field:** value format."""
    text = "**Severity:** high\n"
    assert _extract_bold_field(text, "Severity") == "high"


def test_extract_bold_field_handles_colon_outside_markers() -> None:
    """_extract_bold_field parses **Field**: value format."""
    text = "**Severity**: medium\n"
    assert _extract_bold_field(text, "Severity") == "medium"


def test_extract_bold_field_returns_empty_when_field_absent() -> None:
    """_extract_bold_field returns '' when the field is not found."""
    text = "## Some other text\n"
    assert _extract_bold_field(text, "Severity") == ""


# ===========================================================================
# project_summary — _normalize_severity
# ===========================================================================


def test_normalize_severity_returns_known_keyword() -> None:
    """_normalize_severity returns 'high' when the severity is 'high'."""
    assert _normalize_severity("high") == "high"


def test_normalize_severity_lowercases_input() -> None:
    """_normalize_severity lowercases 'HIGH' to 'high'."""
    assert _normalize_severity("HIGH") == "high"


def test_normalize_severity_strips_markdown_bold() -> None:
    """_normalize_severity handles **high** markdown bold formatting."""
    assert _normalize_severity("**high**") == "high"


def test_normalize_severity_extracts_first_token_from_verbose_value() -> None:
    """_normalize_severity extracts 'medium' from 'medium (data completeness issues)'."""
    assert _normalize_severity("medium (data completeness issues)") == "medium"


def test_normalize_severity_returns_unknown_for_empty_input() -> None:
    """_normalize_severity returns 'unknown' for an empty string."""
    assert _normalize_severity("") == "unknown"


def test_normalize_severity_returns_unknown_for_unrecognized_value() -> None:
    """_normalize_severity returns 'unknown' for unrecognized values."""
    assert _normalize_severity("catastrophic") == "unknown"


def test_normalize_severity_searches_first_80_chars_for_known_keyword() -> None:
    """_normalize_severity finds 'low' embedded in a longer value."""
    assert _normalize_severity("priority: low — some notes here") == "low"


# ===========================================================================
# project_summary — _parse_date_loose
# ===========================================================================


def test_parse_date_loose_parses_iso8601_with_offset() -> None:
    """_parse_date_loose parses a full ISO 8601 datetime with timezone offset."""
    dt = _parse_date_loose("2026-05-24T18:03:44+00:00")
    assert dt is not None
    assert dt.year == 2026


def test_parse_date_loose_parses_z_suffix() -> None:
    """_parse_date_loose handles a Z suffix."""
    dt = _parse_date_loose("2026-06-01T00:00:00Z")
    assert dt is not None
    assert dt.year == 2026


def test_parse_date_loose_parses_yyyy_mm_dd() -> None:
    """_parse_date_loose parses a bare YYYY-MM-DD date."""
    dt = _parse_date_loose("2026-05-15")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 5
    assert dt.day == 15


def test_parse_date_loose_returns_utc_aware_datetime() -> None:
    """_parse_date_loose returns a UTC-aware datetime for naive ISO dates."""
    dt = _parse_date_loose("2026-01-01")
    assert dt is not None
    assert dt.tzinfo is not None


def test_parse_date_loose_returns_none_for_empty_string() -> None:
    """_parse_date_loose returns None for an empty string."""
    assert _parse_date_loose("") is None


def test_parse_date_loose_returns_none_for_garbage_input() -> None:
    """_parse_date_loose returns None for a non-date string."""
    assert _parse_date_loose("not-a-date") is None


# ===========================================================================
# project_summary — _date_from_task_id
# ===========================================================================


def test_date_from_task_id_extracts_date_from_standard_task_id() -> None:
    """_date_from_task_id parses a date from ROLE-YYYYMMDD-NNN-slug format."""
    dt = _date_from_task_id("CODER-20260628-045-cover-reports")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 6
    assert dt.day == 28


def test_date_from_task_id_returns_none_when_no_date_found() -> None:
    """_date_from_task_id returns None when no 8-digit date substring exists."""
    assert _date_from_task_id("TASK-ID-without-date") is None


# ===========================================================================
# project_summary — _truncate
# ===========================================================================


def test_truncate_returns_short_strings_unchanged() -> None:
    """_truncate returns strings shorter than the limit unchanged."""
    assert _truncate("short text") == "short text"


def test_truncate_appends_ellipsis_for_long_strings() -> None:
    """_truncate appends '...' when truncating long strings."""
    long = "x" * 100
    result = _truncate(long, length=10)
    assert result.endswith("...")
    assert len(result) == 10


def test_truncate_strips_leading_and_trailing_whitespace() -> None:
    """_truncate strips surrounding whitespace before checking length."""
    result = _truncate("  hello  ")
    assert result == "hello"


# ===========================================================================
# project_summary — _slug_from_filename
# ===========================================================================


def test_slug_from_filename_strips_bug_prefix_and_id() -> None:
    """_slug_from_filename removes the BUG-NNN- prefix from a bug filename."""
    p = pathlib.Path("BUG-0001-my-bug-title.md")
    slug = _slug_from_filename(p)
    assert "BUG" not in slug
    assert "my bug title" in slug


def test_slug_from_filename_replaces_hyphens_with_spaces() -> None:
    """_slug_from_filename converts hyphens to spaces in the slug."""
    p = pathlib.Path("some-file-name.md")
    slug = _slug_from_filename(p)
    assert "-" not in slug
    assert "some file name" in slug


# ===========================================================================
# project_summary — _safe_read
# ===========================================================================


def test_safe_read_returns_file_content(tmp_path: pathlib.Path) -> None:
    """_safe_read returns the text content of an existing file."""
    f = tmp_path / "test.md"
    f.write_text("hello world", encoding="utf-8")
    assert _safe_read(f) == "hello world"


def test_safe_read_returns_empty_string_for_missing_file(tmp_path: pathlib.Path) -> None:
    """_safe_read returns '' when the file does not exist."""
    assert _safe_read(tmp_path / "nonexistent.md") == ""


# ===========================================================================
# project_summary — _read_ini_value
# ===========================================================================


def test_read_ini_value_returns_value_from_correct_section(tmp_path: pathlib.Path) -> None:
    """_read_ini_value reads a key from the correct INI section."""
    cfg = tmp_path / "project.cfg"
    cfg.write_text(
        "[project]\n"
        "dev_tree_path = /opt/myproject\n"
        "name = test\n",
        encoding="utf-8",
    )
    val = _read_ini_value(cfg, "project", "dev_tree_path")
    assert val == "/opt/myproject"


def test_read_ini_value_returns_empty_when_key_absent(tmp_path: pathlib.Path) -> None:
    """_read_ini_value returns '' when the key does not exist in the section."""
    cfg = tmp_path / "project.cfg"
    cfg.write_text("[project]\nname = test\n", encoding="utf-8")
    val = _read_ini_value(cfg, "project", "missing_key")
    assert val == ""


def test_read_ini_value_returns_empty_when_section_absent(tmp_path: pathlib.Path) -> None:
    """_read_ini_value returns '' when the section does not exist."""
    cfg = tmp_path / "project.cfg"
    cfg.write_text("[other]\nfoo = bar\n", encoding="utf-8")
    val = _read_ini_value(cfg, "project", "foo")
    assert val == ""


def test_read_ini_value_does_not_cross_section_boundary(tmp_path: pathlib.Path) -> None:
    """_read_ini_value stops reading at the next section boundary."""
    cfg = tmp_path / "project.cfg"
    cfg.write_text(
        "[project]\nname = test\n[other]\ndev_tree_path = /wrong\n",
        encoding="utf-8",
    )
    val = _read_ini_value(cfg, "project", "dev_tree_path")
    assert val == ""


# ===========================================================================
# project_summary — BugItem
# ===========================================================================


def _write_bug_file(
    bugs_dir: pathlib.Path,
    filename: str,
    content: str,
) -> pathlib.Path:
    """Write a bug markdown file and return its path."""
    bugs_dir.mkdir(parents=True, exist_ok=True)
    p = bugs_dir / filename
    p.write_text(content, encoding="utf-8")
    return p


def test_bug_item_parses_status_and_severity(tmp_path: pathlib.Path) -> None:
    """BugItem parses Status and Severity fields from a bug markdown file."""
    content = (
        "# A Test Bug\n\n"
        "## Status\nopen\n\n"
        "**Severity:** high\n\n"
        "## Symptom\nSomething is broken\n"
    )
    p = _write_bug_file(tmp_path / "bugs", "BUG-0001-test-bug.md", content)
    bug = BugItem(p)
    assert bug.status == "open"
    assert bug.severity == "high"


def test_bug_item_is_open_for_open_status(tmp_path: pathlib.Path) -> None:
    """BugItem.is_open returns True when status is 'open'."""
    content = "# Bug\n\n## Status\nopen\n"
    p = _write_bug_file(tmp_path / "bugs", "BUG-0002.md", content)
    bug = BugItem(p)
    assert bug.is_open is True


def test_bug_item_is_done_for_done_status(tmp_path: pathlib.Path) -> None:
    """BugItem.is_done returns True when status is 'done'."""
    content = "# Bug\n\n## Status\ndone\n"
    p = _write_bug_file(tmp_path / "bugs", "BUG-0003.md", content)
    bug = BugItem(p)
    assert bug.is_done is True


def test_bug_item_is_wont_fix_for_wont_fix_status(tmp_path: pathlib.Path) -> None:
    """BugItem.is_wont_fix returns True when status is 'wont-fix'."""
    content = "# Bug\n\n## Status\nwont-fix\n"
    p = _write_bug_file(tmp_path / "bugs", "BUG-0004.md", content)
    bug = BugItem(p)
    assert bug.is_wont_fix is True


def test_bug_item_is_wont_do_for_wont_do_status(tmp_path: pathlib.Path) -> None:
    """BugItem.is_wont_do returns True when status is 'wont-do'."""
    content = "# Bug\n\n## Status\nwont-do\n"
    p = _write_bug_file(tmp_path / "bugs", "BUG-0005.md", content)
    bug = BugItem(p)
    assert bug.is_wont_do is True


def test_bug_item_extracts_title_from_h1(tmp_path: pathlib.Path) -> None:
    """BugItem extracts the title from the first H1 heading."""
    content = "# My Important Bug Title\n\n## Status\nopen\n"
    p = _write_bug_file(tmp_path / "bugs", "BUG-0006.md", content)
    bug = BugItem(p)
    assert bug.title == "My Important Bug Title"


def test_bug_item_parses_date_from_bold_field(tmp_path: pathlib.Path) -> None:
    """BugItem parses a date from the **Date:** bold field."""
    content = (
        "# Bug\n\n"
        "## Status\nopen\n\n"
        "**Date:** 2026-05-01\n"
    )
    p = _write_bug_file(tmp_path / "bugs", "BUG-0007.md", content)
    bug = BugItem(p)
    assert bug.date is not None
    assert bug.date.year == 2026


# ===========================================================================
# project_summary — PriorityItem
# ===========================================================================


def _write_priority_file(
    priority_dir: pathlib.Path,
    filename: str,
    content: str,
) -> pathlib.Path:
    priority_dir.mkdir(parents=True, exist_ok=True)
    p = priority_dir / filename
    p.write_text(content, encoding="utf-8")
    return p


def test_priority_item_is_open_for_running_status(tmp_path: pathlib.Path) -> None:
    """PriorityItem.is_open returns True when status is 'running'."""
    content = "# Priority\n\n## Status\nrunning\n"
    p = _write_priority_file(tmp_path / "priority", "PRIORITY-001.md", content)
    item = PriorityItem(p)
    assert item.is_open is True


def test_priority_item_is_done_for_done_status(tmp_path: pathlib.Path) -> None:
    """PriorityItem.is_done returns True when status is 'done'."""
    content = "# Priority\n\n## Status\ndone\n"
    p = _write_priority_file(tmp_path / "priority", "PRIORITY-002.md", content)
    item = PriorityItem(p)
    assert item.is_done is True


# ===========================================================================
# project_summary — RequirementItem
# ===========================================================================


def _write_req_file(
    req_dir: pathlib.Path,
    filename: str,
    content: str,
) -> pathlib.Path:
    req_dir.mkdir(parents=True, exist_ok=True)
    p = req_dir / filename
    p.write_text(content, encoding="utf-8")
    return p


def test_requirement_item_is_shipped_for_done_status(tmp_path: pathlib.Path) -> None:
    """RequirementItem.is_shipped returns True when status is 'done'."""
    content = "# Req\n\n## Status\ndone\n"
    p = _write_req_file(tmp_path / "requirements", "req-001.md", content)
    item = RequirementItem(p)
    assert item.is_shipped is True


def test_requirement_item_is_inflight_for_running_status(tmp_path: pathlib.Path) -> None:
    """RequirementItem.is_inflight returns True when status is 'running'."""
    content = "# Req\n\n## Status\nrunning\n"
    p = _write_req_file(tmp_path / "requirements", "req-002.md", content)
    item = RequirementItem(p)
    assert item.is_inflight is True


def test_requirement_item_is_queued_for_open_status(tmp_path: pathlib.Path) -> None:
    """RequirementItem.is_queued returns True when status is 'open'."""
    content = "# Req\n\n## Status\nopen\n"
    p = _write_req_file(tmp_path / "requirements", "req-003.md", content)
    item = RequirementItem(p)
    assert item.is_queued is True


# ===========================================================================
# project_summary — ReleaseNote
# ===========================================================================


def _write_release_file(
    release_dir: pathlib.Path,
    filename: str,
    content: str,
) -> pathlib.Path:
    release_dir.mkdir(parents=True, exist_ok=True)
    p = release_dir / filename
    p.write_text(content, encoding="utf-8")
    return p


def test_release_note_extracts_version_from_filename(tmp_path: pathlib.Path) -> None:
    """ReleaseNote extracts the version from a 'v0.23.31'-style filename."""
    content = "# Release Notes\n\n**Release Date:** 2026-01-15\n"
    p = _write_release_file(tmp_path / "release-notes", "v0.23.31.md", content)
    rn = ReleaseNote(p)
    assert rn.version == "v0.23.31"


def test_release_note_version_tuple_parses_correctly(tmp_path: pathlib.Path) -> None:
    """ReleaseNote.version_tuple returns numeric tuple for version sorting."""
    content = "# Release Notes\n"
    p = _write_release_file(tmp_path / "release-notes", "v1.2.3.md", content)
    rn = ReleaseNote(p)
    assert rn.version_tuple() == (1, 2, 3)


def test_release_note_parses_release_date(tmp_path: pathlib.Path) -> None:
    """ReleaseNote parses the release date from the **Release Date:** field."""
    content = "# Release Notes\n\n**Release Date:** 2026-05-15\n"
    p = _write_release_file(tmp_path / "release-notes", "v0.50.0.md", content)
    rn = ReleaseNote(p)
    assert rn.release_date is not None
    assert rn.release_date.year == 2026


# ===========================================================================
# project_summary — TaskEvent
# ===========================================================================


def test_task_event_extracts_state_and_role(tmp_path: pathlib.Path) -> None:
    """TaskEvent extracts State and Role from a status.md file."""
    tasks_dir = tmp_path / "tasks" / "CODER-20260628-001-example"
    tasks_dir.mkdir(parents=True)
    status = tasks_dir / "status.md"
    status.write_text(
        "## Task\nCODER-20260628-001-example\n\n"
        "## State\nDONE\n\n"
        "## Role\nCODER\n\n"
        "## Summary\nCompleted the work.\n",
        encoding="utf-8",
    )
    ev = TaskEvent(status)
    assert ev.state == "DONE"
    assert ev.role == "CODER"


def test_task_event_extracts_date_from_task_id(tmp_path: pathlib.Path) -> None:
    """TaskEvent derives the event date from the task ID's embedded YYYYMMDD."""
    tasks_dir = tmp_path / "tasks" / "PM-20260601-003-plan-rc"
    tasks_dir.mkdir(parents=True)
    status = tasks_dir / "status.md"
    status.write_text(
        "## Task\nPM-20260601-003-plan-rc\n\n## State\nDONE\n",
        encoding="utf-8",
    )
    ev = TaskEvent(status)
    assert ev.date is not None
    assert ev.date.year == 2026
    assert ev.date.month == 6


# ===========================================================================
# project_summary — ProjectReport computed views
# ===========================================================================


def _make_bug(tmp_path: pathlib.Path, name: str, status: str, severity: str = "medium") -> BugItem:
    content = f"# Bug {name}\n\n## Status\n{status}\n\n**Severity:** {severity}\n"
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    p = bugs_dir / f"{name}.md"
    p.write_text(content, encoding="utf-8")
    return BugItem(p)


def _make_priority(tmp_path: pathlib.Path, name: str, status: str) -> PriorityItem:
    content = f"# Priority {name}\n\n## Status\n{status}\n"
    pdir = tmp_path / "priority"
    pdir.mkdir(parents=True, exist_ok=True)
    p = pdir / f"{name}.md"
    p.write_text(content, encoding="utf-8")
    return PriorityItem(p)


def _make_requirement(tmp_path: pathlib.Path, name: str, status: str) -> RequirementItem:
    content = f"# Req {name}\n\n## Status\n{status}\n"
    rdir = tmp_path / "requirements"
    rdir.mkdir(parents=True, exist_ok=True)
    p = rdir / f"{name}.md"
    p.write_text(content, encoding="utf-8")
    return RequirementItem(p)


def _make_release(tmp_path: pathlib.Path, version: str, date_str: str) -> ReleaseNote:
    content = f"# Release Notes {version}\n\n**Release Date:** {date_str}\n"
    rdir = tmp_path / "release-notes"
    rdir.mkdir(parents=True, exist_ok=True)
    p = rdir / f"{version}.md"
    p.write_text(content, encoding="utf-8")
    return ReleaseNote(p)


def test_project_report_open_bugs_filters_by_open_status(tmp_path: pathlib.Path) -> None:
    """ProjectReport.open_bugs returns only bugs with open/blocked status."""
    bugs = [
        _make_bug(tmp_path / "b1", "BUG-001", "open"),
        _make_bug(tmp_path / "b2", "BUG-002", "done"),
        _make_bug(tmp_path / "b3", "BUG-003", "blocked"),
    ]
    report = ProjectReport("test", bugs, [], [], [], [])
    open_bugs = report.open_bugs()
    assert len(open_bugs) == 2
    statuses = {b.status for b in open_bugs}
    assert "open" in statuses
    assert "blocked" in statuses


def test_project_report_done_bugs_filters_correctly(tmp_path: pathlib.Path) -> None:
    """ProjectReport.done_bugs returns only bugs with done status."""
    bugs = [
        _make_bug(tmp_path / "b1", "BUG-001", "open"),
        _make_bug(tmp_path / "b2", "BUG-002", "done"),
    ]
    report = ProjectReport("test", bugs, [], [], [], [])
    done = report.done_bugs()
    assert len(done) == 1
    assert done[0].status == "done"


def test_project_report_wont_fix_bugs_includes_wont_do(tmp_path: pathlib.Path) -> None:
    """ProjectReport.wont_fix_bugs includes both wont-fix and wont-do bugs."""
    bugs = [
        _make_bug(tmp_path / "b1", "BUG-001", "wont-fix"),
        _make_bug(tmp_path / "b2", "BUG-002", "wont-do"),
        _make_bug(tmp_path / "b3", "BUG-003", "open"),
    ]
    report = ProjectReport("test", bugs, [], [], [], [])
    wont = report.wont_fix_bugs()
    assert len(wont) == 2


def test_project_report_current_version_returns_last_release(tmp_path: pathlib.Path) -> None:
    """ProjectReport.current_version returns the version of the last release."""
    releases = [
        _make_release(tmp_path / "r1", "v1.0.0", "2026-01-01"),
        _make_release(tmp_path / "r2", "v1.1.0", "2026-06-01"),
    ]
    # Sort by version_tuple as the loader does
    releases.sort(key=lambda r: r.version_tuple())
    report = ProjectReport("test", [], [], [], releases, [])
    assert report.current_version() == "v1.1.0"


def test_project_report_current_version_returns_none_when_no_releases() -> None:
    """ProjectReport.current_version returns 'none' when no releases exist."""
    report = ProjectReport("test", [], [], [], [], [])
    assert report.current_version() == "none"


def test_project_report_bugs_by_severity_counts_correctly(tmp_path: pathlib.Path) -> None:
    """ProjectReport.bugs_by_severity returns counts grouped by severity level."""
    bugs = [
        _make_bug(tmp_path / "b1", "BUG-001", "open", severity="high"),
        _make_bug(tmp_path / "b2", "BUG-002", "open", severity="high"),
        _make_bug(tmp_path / "b3", "BUG-003", "done", severity="low"),
    ]
    report = ProjectReport("test", bugs, [], [], [], [])
    counts = report.bugs_by_severity()
    assert counts.get("high") == 2
    assert counts.get("low") == 1


def test_project_report_oldest_open_bug_returns_earliest_dated(tmp_path: pathlib.Path) -> None:
    """ProjectReport.oldest_open_bug returns the bug with the earliest date."""
    # Write bugs with different dates embedded in content
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    b1 = bugs_dir / "BUG-001.md"
    b1.write_text("# Bug 1\n\n## Status\nopen\n\n**Date:** 2026-03-01\n", encoding="utf-8")
    b2 = bugs_dir / "BUG-002.md"
    b2.write_text("# Bug 2\n\n## Status\nopen\n\n**Date:** 2026-01-01\n", encoding="utf-8")
    bug1, bug2 = BugItem(b1), BugItem(b2)
    report = ProjectReport("test", [bug1, bug2], [], [], [], [])
    oldest = report.oldest_open_bug()
    assert oldest is not None
    assert oldest.date is not None
    assert oldest.date.month == 1  # January is earliest


def test_project_report_oldest_open_bug_returns_none_when_no_open_bugs(
    tmp_path: pathlib.Path,
) -> None:
    """ProjectReport.oldest_open_bug returns None when no bugs are open."""
    bugs = [_make_bug(tmp_path / "b1", "BUG-001", "done")]
    report = ProjectReport("test", bugs, [], [], [], [])
    assert report.oldest_open_bug() is None


def test_project_report_most_recent_bug_returns_latest_dated(tmp_path: pathlib.Path) -> None:
    """ProjectReport.most_recent_bug returns the bug with the most recent date."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    b1 = bugs_dir / "BUG-001.md"
    b1.write_text("# Bug 1\n\n## Status\nopen\n\n**Date:** 2026-01-01\n", encoding="utf-8")
    b2 = bugs_dir / "BUG-002.md"
    b2.write_text("# Bug 2\n\n## Status\nopen\n\n**Date:** 2026-06-01\n", encoding="utf-8")
    bug1, bug2 = BugItem(b1), BugItem(b2)
    report = ProjectReport("test", [bug1, bug2], [], [], [], [])
    recent = report.most_recent_bug()
    assert recent is not None
    assert recent.date is not None
    assert recent.date.month == 6


def test_project_report_recent_events_sorts_descending_by_date(tmp_path: pathlib.Path) -> None:
    """ProjectReport.recent_events returns events in reverse chronological order."""
    def _make_event(task_id: str) -> TaskEvent:
        tdir = tmp_path / "tasks" / task_id
        tdir.mkdir(parents=True, exist_ok=True)
        sf = tdir / "status.md"
        sf.write_text(
            f"## Task\n{task_id}\n\n## State\nDONE\n",
            encoding="utf-8",
        )
        return TaskEvent(sf)

    ev1 = _make_event("CODER-20260101-001-first")
    ev2 = _make_event("CODER-20260601-002-latest")
    ev3 = _make_event("CODER-20260301-003-middle")
    report = ProjectReport("test", [], [], [], [], [ev1, ev2, ev3])
    events = report.recent_events(10)
    # Latest should be first
    assert events[0].date > events[1].date


def test_project_report_time_since_last_release_returns_unknown_when_no_releases() -> None:
    """ProjectReport.time_since_last_release returns 'unknown' when no releases exist."""
    report = ProjectReport("test", [], [], [], [], [])
    assert report.time_since_last_release() == "unknown"


# ===========================================================================
# project_summary — apply_offline_summaries
# ===========================================================================


def test_apply_offline_summaries_fills_oneliner_for_bugs(tmp_path: pathlib.Path) -> None:
    """apply_offline_summaries populates the oneliner field for BugItem instances."""
    content = "# My Bug Title\n\n## Status\nopen\n\n## Symptom\nThe widget crashes on startup.\n"
    p = _write_bug_file(tmp_path / "bugs", "BUG-0001.md", content)
    bug = BugItem(p)
    apply_offline_summaries([bug], [], [], [])
    assert bug.oneliner != ""
    assert len(bug.oneliner) <= 80


def test_apply_offline_summaries_fills_oneliner_for_priorities(tmp_path: pathlib.Path) -> None:
    """apply_offline_summaries populates the oneliner field for PriorityItem instances."""
    content = "# Improve startup time\n\n## Status\nopen\n"
    p = _write_priority_file(tmp_path / "priority", "PRIORITY-001.md", content)
    prio = PriorityItem(p)
    apply_offline_summaries([], [prio], [], [])
    assert prio.oneliner != ""


def test_apply_offline_summaries_fills_oneliner_for_requirements(tmp_path: pathlib.Path) -> None:
    """apply_offline_summaries populates the oneliner for RequirementItem instances."""
    content = "# New Authentication Feature\n\n## Status\nrunning\n"
    p = _write_req_file(tmp_path / "requirements", "req-001.md", content)
    req = RequirementItem(p)
    apply_offline_summaries([], [], [req], [])
    assert req.oneliner != ""


def test_apply_offline_summaries_fills_oneliner_for_releases(tmp_path: pathlib.Path) -> None:
    """apply_offline_summaries populates the oneliner for ReleaseNote instances."""
    content = "# Release Notes v1.0.0\n\n## Summary\nFirst stable release.\n\n**Release Date:** 2026-01-01\n"
    p = _write_release_file(tmp_path / "release-notes", "v1.0.0.md", content)
    rn = ReleaseNote(p)
    apply_offline_summaries([], [], [], [rn])
    assert rn.oneliner != ""


# ===========================================================================
# project_summary — render_project_report (text format)
# ===========================================================================


def _build_sample_report(tmp_path: pathlib.Path) -> ProjectReport:
    """Build a ProjectReport with diverse fixture data for rendering tests."""
    bugs = [
        _make_bug(tmp_path / "b1", "BUG-001", "open", "high"),
        _make_bug(tmp_path / "b2", "BUG-002", "done", "low"),
        _make_bug(tmp_path / "b3", "BUG-003", "wont-fix", "medium"),
    ]
    priorities = [
        _make_priority(tmp_path / "p1", "PRIORITY-001", "running"),
        _make_priority(tmp_path / "p2", "PRIORITY-002", "done"),
    ]
    requirements = [
        _make_requirement(tmp_path / "r1", "req-shipped", "done"),
        _make_requirement(tmp_path / "r2", "req-inflight", "running"),
        _make_requirement(tmp_path / "r3", "req-queued", "open"),
    ]
    releases = [
        _make_release(tmp_path / "rn1", "v1.0.0", "2026-01-01"),
    ]
    apply_offline_summaries(bugs, priorities, requirements, releases)
    return ProjectReport("myproject", bugs, priorities, requirements, releases, [])


def test_render_project_report_text_contains_project_name(tmp_path: pathlib.Path) -> None:
    """render_project_report (text) includes the project name in the output."""
    report = _build_sample_report(tmp_path)
    output = render_project_report(report, fmt="text")
    assert "myproject" in output


def test_render_project_report_text_contains_bug_counts(tmp_path: pathlib.Path) -> None:
    """render_project_report (text) includes total, open, and resolved bug counts."""
    report = _build_sample_report(tmp_path)
    output = render_project_report(report, fmt="text")
    assert "total=" in output
    assert "open=" in output
    assert "resolved=" in output


def test_render_project_report_text_contains_requirements_section(tmp_path: pathlib.Path) -> None:
    """render_project_report (text) includes a Requirements section."""
    report = _build_sample_report(tmp_path)
    output = render_project_report(report, fmt="text")
    assert "Requirements" in output
    assert "shipped=" in output
    assert "in-flight=" in output


def test_render_project_report_brief_mode_omits_per_item_listings(
    tmp_path: pathlib.Path,
) -> None:
    """render_project_report with brief=True omits per-item summaries."""
    report = _build_sample_report(tmp_path)
    full_output = render_project_report(report, fmt="text", brief=False)
    brief_output = render_project_report(report, fmt="text", brief=True)
    # Brief should be shorter than full
    assert len(brief_output) < len(full_output)


def test_render_project_report_md_format_uses_markdown_headings(
    tmp_path: pathlib.Path,
) -> None:
    """render_project_report (md) uses ## headings instead of separator lines."""
    report = _build_sample_report(tmp_path)
    output = render_project_report(report, fmt="md")
    assert "## Requirements" in output
    assert "## Bugs" in output


def test_render_project_report_md_format_uses_bullet_dashes(tmp_path: pathlib.Path) -> None:
    """render_project_report (md) uses '- ' for bullets rather than '* '."""
    report = _build_sample_report(tmp_path)
    output = render_project_report(report, fmt="md", brief=False)
    # md format uses "- " prefix
    assert "- " in output


def test_render_project_report_json_format_returns_valid_json(tmp_path: pathlib.Path) -> None:
    """render_project_report (json) returns valid JSON."""
    report = _build_sample_report(tmp_path)
    output = render_project_report(report, fmt="json")
    data = json.loads(output)
    assert "project" in data
    assert data["project"] == "myproject"


def test_render_project_report_json_contains_bug_counts(tmp_path: pathlib.Path) -> None:
    """render_project_report (json) includes bug count fields."""
    report = _build_sample_report(tmp_path)
    output = render_project_report(report, fmt="json")
    data = json.loads(output)
    assert "bugs" in data
    assert "total" in data["bugs"]
    assert "open" in data["bugs"]


def test_render_project_report_json_brief_omits_open_items(tmp_path: pathlib.Path) -> None:
    """render_project_report (json, brief=True) does not include per-item arrays."""
    report = _build_sample_report(tmp_path)
    output = render_project_report(report, fmt="json", brief=True)
    data = json.loads(output)
    # brief mode should not include open_items arrays
    assert "open_items" not in data.get("bugs", {})


def test_render_project_report_show_all_overrides_truncation(tmp_path: pathlib.Path) -> None:
    """render_project_report with show_all=True shows all items without truncation."""
    # Create enough open bugs to trigger truncation in default mode
    bugs = []
    for i in range(8):
        bugs.append(_make_bug(tmp_path / f"b{i}", f"BUG-{i:03d}", "open"))
    apply_offline_summaries(bugs, [], [], [])
    report = ProjectReport("test", bugs, [], [], [], [])

    default_output = render_project_report(report, fmt="text")
    all_output = render_project_report(report, fmt="text", show_all=True)
    # show_all output should be longer (more items shown)
    assert len(all_output) >= len(default_output)


def test_render_project_report_open_bugs_with_limit_shows_more_ellipsis(
    tmp_path: pathlib.Path,
) -> None:
    """render_project_report shows '... and N more' when open bugs exceed limit."""
    bugs = []
    for i in range(7):
        bugs.append(_make_bug(tmp_path / f"b{i}", f"BUG-{i:03d}", "open"))
    apply_offline_summaries(bugs, [], [], [])
    report = ProjectReport("test", bugs, [], [], [], [])
    output = render_project_report(report, fmt="text", brief=False)
    assert "more" in output


# ===========================================================================
# project_summary — render_aggregate
# ===========================================================================


def test_render_aggregate_text_contains_project_count(tmp_path: pathlib.Path) -> None:
    """render_aggregate (text) includes the number of projects."""
    r1 = ProjectReport("proj-a", [], [], [], [], [])
    r2 = ProjectReport("proj-b", [], [], [], [], [])
    output = render_aggregate([r1, r2], fmt="text")
    assert "2" in output
    assert "Projects" in output


def test_render_aggregate_json_returns_valid_json(tmp_path: pathlib.Path) -> None:
    """render_aggregate (json) returns parseable JSON with aggregate key."""
    bugs = [_make_bug(tmp_path / "b1", "BUG-001", "open")]
    r1 = ProjectReport("proj-a", bugs, [], [], [], [])
    r2 = ProjectReport("proj-b", [], [], [], [], [])
    output = render_aggregate([r1, r2], fmt="json")
    data = json.loads(output)
    assert "aggregate" in data
    assert data["aggregate"]["total_bugs"] == 1


def test_render_aggregate_sums_open_bugs_across_projects(tmp_path: pathlib.Path) -> None:
    """render_aggregate counts total open bugs across all projects."""
    bugs_a = [_make_bug(tmp_path / "ba1", "BUG-001", "open")]
    bugs_b = [
        _make_bug(tmp_path / "bb1", "BUG-002", "open"),
        _make_bug(tmp_path / "bb2", "BUG-003", "done"),
    ]
    r1 = ProjectReport("a", bugs_a, [], [], [], [])
    r2 = ProjectReport("b", bugs_b, [], [], [], [])
    output = render_aggregate([r1, r2], fmt="json")
    data = json.loads(output)
    assert data["aggregate"]["total_bugs"] == 3
    assert data["aggregate"]["open_bugs"] == 2


# ===========================================================================
# project_summary — _load_bugs / _load_priorities / _load_requirements
# ===========================================================================


def test_load_bugs_returns_all_md_files_as_bug_items(tmp_path: pathlib.Path) -> None:
    """_load_bugs returns a BugItem for each .md file in the bugs/ directory."""
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir()
    (bugs_dir / "BUG-0001-first.md").write_text("## Status\nopen\n", encoding="utf-8")
    (bugs_dir / "BUG-0002-second.md").write_text("## Status\ndone\n", encoding="utf-8")
    bugs = _load_bugs(tmp_path)
    assert len(bugs) == 2


def test_load_bugs_returns_empty_list_when_no_bugs_dir(tmp_path: pathlib.Path) -> None:
    """_load_bugs returns [] when the bugs/ directory does not exist."""
    assert _load_bugs(tmp_path) == []


def test_load_priorities_skips_template_files(tmp_path: pathlib.Path) -> None:
    """_load_priorities skips files with 'template' in the name."""
    pdir = tmp_path / "priority"
    pdir.mkdir()
    (pdir / "PRIORITY-001.md").write_text("## Status\nopen\n", encoding="utf-8")
    (pdir / "template.md").write_text("## Status\nopen\n", encoding="utf-8")
    items = _load_priorities(tmp_path)
    assert len(items) == 1
    assert items[0].path.name == "PRIORITY-001.md"


def test_load_requirements_skips_readme_files(tmp_path: pathlib.Path) -> None:
    """_load_requirements skips files with 'readme' in the name."""
    rdir = tmp_path / "requirements"
    rdir.mkdir()
    (rdir / "req-001.md").write_text("## Status\nrunning\n", encoding="utf-8")
    (rdir / "README.md").write_text("# Requirements Overview\n", encoding="utf-8")
    items = _load_requirements(tmp_path)
    assert len(items) == 1
    assert items[0].path.name == "req-001.md"


# ===========================================================================
# project_summary — _load_task_events
# ===========================================================================


def test_load_task_events_returns_task_events_from_tasks_dir(tmp_path: pathlib.Path) -> None:
    """_load_task_events returns TaskEvent instances from tasks/*/status.md."""
    tasks_dir = tmp_path / "tasks"
    for tid in ("CODER-20260601-001-foo", "PM-20260601-002-bar"):
        tdir = tasks_dir / tid
        tdir.mkdir(parents=True)
        (tdir / "status.md").write_text(
            f"## Task\n{tid}\n\n## State\nDONE\n",
            encoding="utf-8",
        )
    events = _load_task_events(tmp_path, cutoff=None)
    assert len(events) == 2


def test_load_task_events_filters_by_cutoff(tmp_path: pathlib.Path) -> None:
    """_load_task_events excludes events before the cutoff datetime."""
    tasks_dir = tmp_path / "tasks"
    # Old task (date from ID = 2026-01-01)
    old_dir = tasks_dir / "CODER-20260101-001-old"
    old_dir.mkdir(parents=True)
    (old_dir / "status.md").write_text(
        "## Task\nCODER-20260101-001-old\n\n## State\nDONE\n",
        encoding="utf-8",
    )
    # New task (date from ID = 2026-06-01)
    new_dir = tasks_dir / "CODER-20260601-002-new"
    new_dir.mkdir(parents=True)
    (new_dir / "status.md").write_text(
        "## Task\nCODER-20260601-002-new\n\n## State\nDONE\n",
        encoding="utf-8",
    )
    cutoff = datetime(2026, 3, 1, tzinfo=timezone.utc)
    events = _load_task_events(tmp_path, cutoff=cutoff)
    assert len(events) == 1
    assert "20260601" in events[0].task_id


# ===========================================================================
# project_summary — _list_projects
# ===========================================================================


def test_list_projects_returns_sorted_project_names(tmp_path: pathlib.Path) -> None:
    """_list_projects returns a sorted list of non-hidden directories under projects/."""
    projects_dir = tmp_path / "projects"
    (projects_dir / "zebra-project").mkdir(parents=True)
    (projects_dir / "alpha-project").mkdir(parents=True)
    (projects_dir / ".hidden").mkdir(parents=True)
    names = _list_projects(tmp_path)
    assert names == ["alpha-project", "zebra-project"]


def test_list_projects_returns_empty_when_no_projects_dir(tmp_path: pathlib.Path) -> None:
    """_list_projects returns [] when no projects/ directory exists."""
    assert _list_projects(tmp_path) == []


# ===========================================================================
# project_summary — _fmt_date, _hr, _section_header, _bullet, _sort_key_date
# ===========================================================================


def test_fmt_date_formats_datetime_as_yyyy_mm_dd() -> None:
    """_fmt_date returns 'YYYY-MM-DD' formatted string."""
    dt = datetime(2026, 6, 15, tzinfo=timezone.utc)
    assert _fmt_date(dt) == "2026-06-15"


def test_fmt_date_returns_unknown_for_none() -> None:
    """_fmt_date returns 'unknown' when passed None."""
    assert _fmt_date(None) == "unknown"


def test_hr_returns_dashes_of_default_width() -> None:
    """_hr returns a 70-character dash line by default."""
    line = _hr()
    assert len(line) == 70
    assert all(c == "-" for c in line)


def test_section_header_text_format_includes_title() -> None:
    """_section_header (text) includes the section title surrounded by hr lines."""
    result = _section_header("My Section", "text")
    assert "My Section" in result
    assert "---" in result


def test_section_header_md_format_uses_double_hash() -> None:
    """_section_header (md) returns a ## heading."""
    result = _section_header("My Section", "md")
    assert result.strip().startswith("## My Section")


def test_bullet_md_format_uses_dash_prefix() -> None:
    """_bullet (md) prefixes items with '- '."""
    result = _bullet("item text", "md")
    assert result.startswith("- ")


def test_bullet_text_format_uses_asterisk_prefix() -> None:
    """_bullet (text) prefixes items with '  * '."""
    result = _bullet("item text", "text")
    assert "* " in result


def test_sort_key_date_returns_epoch_for_none() -> None:
    """_sort_key_date returns the epoch datetime (1970-01-01) for None input."""
    epoch = _sort_key_date(None)
    assert epoch.year == 1970


def test_sort_key_date_returns_datetime_unchanged() -> None:
    """_sort_key_date returns the datetime unchanged when it is not None."""
    dt = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert _sort_key_date(dt) == dt


# ===========================================================================
# project_summary — _load_release_notes (deduplication)
# ===========================================================================


def test_load_release_notes_deduplicates_by_filename(tmp_path: pathlib.Path) -> None:
    """_load_release_notes deduplicates files with the same name from multiple locations."""
    # Create a release note in project-local release-notes/ and kanban-level
    proj_root = tmp_path / "projects" / "myproject"
    proj_rn = proj_root / "release-notes"
    proj_rn.mkdir(parents=True)
    (proj_rn / "v1.0.0.md").write_text(
        "# Release Notes\n\n**Release Date:** 2026-01-01\n",
        encoding="utf-8",
    )
    kanban_rn = tmp_path / "release-notes"
    kanban_rn.mkdir()
    (kanban_rn / "v1.0.0.md").write_text(
        "# Release Notes\n\n**Release Date:** 2026-01-01\n",
        encoding="utf-8",
    )
    releases = _load_release_notes(tmp_path, proj_root)
    # Should appear only once despite being in both directories
    versions = [r.version for r in releases]
    assert versions.count("v1.0.0") == 1


def test_load_release_notes_returns_empty_when_no_release_dirs(tmp_path: pathlib.Path) -> None:
    """_load_release_notes returns [] when no release-notes directories exist."""
    proj_root = tmp_path / "projects" / "myproject"
    proj_root.mkdir(parents=True)
    releases = _load_release_notes(tmp_path, proj_root)
    assert releases == []
