"""
test_halt_after.py
==================
Behavioral unit tests for the pgai_agent_kanban.halt_after package.

Covers:
  - halt_after/token.py  — parse_token: empty default, normalization, versioned
                           rc:vX.Y.Z, invalid token → None
  - halt_after/promote.py — promote: HALT created, HALT-AFTER removed,
                            audit entry appended; graceful behavior when
                            HALT-AFTER or release-state.md is absent
  - halt_after/drain.py  — check_drain: versioned rc (drained/not drained),
                           bare rc with no active RC (an earlier defect — promote
                           immediately), role tokens when no pending tasks,
                           pm-in-flight blocks role drain, unsupported token
                           raises ValueError

All tests use tmp_path; no live filesystem paths.
"""

from __future__ import annotations

import pathlib
from pathlib import Path

import pytest

from pgai_agent_kanban.halt_after.token import parse_token
from pgai_agent_kanban.halt_after.promote import (
    _build_audit_entry,
    _event_drained_note,
    promote,
)
from pgai_agent_kanban.halt_after.drain import (
    _has_in_flight_pm,
    _has_pending_role,
    _has_working_role,
    _parse_status_md,
    check_drain,
)


# ---------------------------------------------------------------------------
# halt_after/token — parse_token
# ---------------------------------------------------------------------------


def test_parse_token_empty_string_defaults_to_rc() -> None:
    """parse_token returns 'rc' for an empty content string."""
    assert parse_token("") == "rc"


def test_parse_token_whitespace_only_defaults_to_rc() -> None:
    """parse_token returns 'rc' for content that is only whitespace."""
    assert parse_token("   \n\t  ") == "rc"


def test_parse_token_normalizes_uppercase_to_lowercase() -> None:
    """parse_token lowercases 'RC' to 'rc'."""
    assert parse_token("RC\n") == "rc"


def test_parse_token_strips_surrounding_whitespace() -> None:
    """parse_token strips leading and trailing whitespace before classifying."""
    assert parse_token("  coder  \n") == "coder"


def test_parse_token_returns_pm_token() -> None:
    """parse_token returns 'pm' for 'pm' content."""
    assert parse_token("pm") == "pm"


def test_parse_token_returns_coder_token() -> None:
    """parse_token returns 'coder' for 'coder' content."""
    assert parse_token("coder") == "coder"


def test_parse_token_returns_writer_token() -> None:
    """parse_token returns 'writer' for 'writer' content."""
    assert parse_token("writer") == "writer"


def test_parse_token_returns_tester_token() -> None:
    """parse_token returns 'tester' for 'tester' content."""
    assert parse_token("tester") == "tester"


def test_parse_token_returns_cm_token() -> None:
    """parse_token returns 'cm' for 'cm' content."""
    assert parse_token("cm") == "cm"


def test_parse_token_returns_versioned_rc_token() -> None:
    """parse_token returns 'rc:v0.40.0' for versioned rc content."""
    assert parse_token("rc:v0.40.0") == "rc:v0.40.0"


def test_parse_token_handles_versioned_rc_with_uppercase_rc_prefix() -> None:
    """parse_token lowercases the 'RC:' prefix for versioned rc tokens."""
    result = parse_token("RC:v1.2.3")
    assert result is not None
    assert result.startswith("rc:")


def test_parse_token_returns_none_for_unknown_token() -> None:
    """parse_token returns None for tokens not in the supported set."""
    assert parse_token("deploy") is None


def test_parse_token_returns_none_for_arbitrary_text() -> None:
    """parse_token returns None for arbitrary unrecognized text."""
    assert parse_token("stop-everything") is None


def test_parse_token_returns_none_for_partial_match() -> None:
    """parse_token returns None for 'coder2' (not in supported set)."""
    assert parse_token("coder2") is None


# ---------------------------------------------------------------------------
# halt_after/promote — _event_drained_note and _build_audit_entry
# ---------------------------------------------------------------------------


def test_event_drained_note_for_bare_rc() -> None:
    """_event_drained_note returns the 'Active RC cleared' note for bare 'rc'."""
    note = _event_drained_note("rc")
    assert "Active RC cleared" in note


def test_event_drained_note_for_versioned_rc() -> None:
    """_event_drained_note includes the version for versioned rc tokens."""
    note = _event_drained_note("rc:v0.42.0")
    assert "v0.42.0" in note


def test_event_drained_note_for_coder() -> None:
    """_event_drained_note mentions 'CODER tasks' for the coder event."""
    note = _event_drained_note("coder")
    assert "CODER" in note


def test_build_audit_entry_contains_halt_event_heading() -> None:
    """_build_audit_entry includes the '## HALT Event' heading."""
    entry = _build_audit_entry("rc", "2026-06-01T12:00:00+00:00")
    assert "## HALT Event" in entry


def test_build_audit_entry_contains_supplied_timestamp() -> None:
    """_build_audit_entry includes the supplied timestamp in the Timestamp field."""
    ts = "2026-06-01T12:00:00+00:00"
    entry = _build_audit_entry("rc", ts)
    assert ts in entry


def test_build_audit_entry_contains_trigger_field() -> None:
    """_build_audit_entry includes a Trigger field."""
    entry = _build_audit_entry("coder", "2026-06-01T12:00:00+00:00")
    assert "Trigger:" in entry


def test_build_audit_entry_contains_promoted_field() -> None:
    """_build_audit_entry includes the 'Promoted: HALT-AFTER → HALT' field."""
    entry = _build_audit_entry("rc", "2026-06-01T12:00:00+00:00")
    assert "HALT-AFTER" in entry
    assert "HALT" in entry


# ---------------------------------------------------------------------------
# halt_after/promote — promote filesystem behavior
# ---------------------------------------------------------------------------


def test_promote_creates_halt_sentinel(tmp_path: Path) -> None:
    """promote creates a HALT file in project_root."""
    (tmp_path / "HALT-AFTER").write_text("rc\n", encoding="utf-8")
    (tmp_path / "release-state.md").write_text("# Release State\n", encoding="utf-8")
    promote(tmp_path, "rc")
    assert (tmp_path / "HALT").exists()


def test_promote_removes_halt_after_sentinel(tmp_path: Path) -> None:
    """promote removes the HALT-AFTER file after creating HALT."""
    (tmp_path / "HALT-AFTER").write_text("rc\n", encoding="utf-8")
    (tmp_path / "release-state.md").write_text("# Release State\n", encoding="utf-8")
    promote(tmp_path, "rc")
    assert not (tmp_path / "HALT-AFTER").exists()


def test_promote_appends_audit_entry_to_release_state_md(tmp_path: Path) -> None:
    """promote appends a ## HALT Event audit block to release-state.md."""
    (tmp_path / "HALT-AFTER").write_text("rc\n", encoding="utf-8")
    (tmp_path / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n", encoding="utf-8"
    )
    promote(tmp_path, "rc")
    content = (tmp_path / "release-state.md").read_text(encoding="utf-8")
    assert "## HALT Event" in content


def test_promote_continues_when_halt_after_absent(tmp_path: Path) -> None:
    """promote proceeds gracefully when HALT-AFTER is already absent."""
    (tmp_path / "release-state.md").write_text("# Release State\n", encoding="utf-8")
    # No HALT-AFTER — promote should log a warning but not raise
    promote(tmp_path, "rc")
    assert (tmp_path / "HALT").exists()


def test_promote_continues_when_release_state_md_absent(tmp_path: Path) -> None:
    """promote creates HALT and removes HALT-AFTER even when release-state.md is absent."""
    (tmp_path / "HALT-AFTER").write_text("rc\n", encoding="utf-8")
    # No release-state.md — promote should warn but not raise
    promote(tmp_path, "rc")
    assert (tmp_path / "HALT").exists()
    assert not (tmp_path / "HALT-AFTER").exists()


def test_promote_halt_event_entry_includes_event_token(tmp_path: Path) -> None:
    """promote writes an audit entry that references the event token."""
    (tmp_path / "HALT-AFTER").write_text("coder\n", encoding="utf-8")
    (tmp_path / "release-state.md").write_text("# Release State\n", encoding="utf-8")
    promote(tmp_path, "coder")
    content = (tmp_path / "release-state.md").read_text(encoding="utf-8")
    assert "coder" in content


# ---------------------------------------------------------------------------
# halt_after/drain — _parse_status_md
# ---------------------------------------------------------------------------


def test_parse_status_md_extracts_state_and_role() -> None:
    """_parse_status_md returns the State and Role values from status.md content."""
    text = "## State\nWORKING\n\n## Role\nCODER\n"
    result = _parse_status_md(text)
    assert result["State"] == "WORKING"
    assert result["Role"] == "CODER"


def test_parse_status_md_returns_empty_strings_when_headings_absent() -> None:
    """_parse_status_md returns empty strings for State and Role when headings are absent."""
    text = "## Summary\nSome text\n"
    result = _parse_status_md(text)
    assert result["State"] == ""
    assert result["Role"] == ""


# ---------------------------------------------------------------------------
# halt_after/drain — _has_working_role, _has_pending_role, _has_in_flight_pm
# ---------------------------------------------------------------------------


def _write_task_status(
    tasks_dir: Path, task_id: str, state: str, role: str
) -> None:
    """Write a minimal status.md for a task under tasks_dir."""
    task_dir = tasks_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "status.md").write_text(
        f"## State\n{state}\n\n## Role\n{role}\n",
        encoding="utf-8",
    )


def test_has_working_role_returns_true_when_working_task_exists(
    tmp_path: Path,
) -> None:
    """_has_working_role returns True when a task with State=WORKING and the given role exists."""
    _write_task_status(tmp_path / "tasks", "CODER-001", "WORKING", "CODER")
    assert _has_working_role(tmp_path, "coder") is True


def test_has_working_role_returns_false_when_no_working_task_exists(
    tmp_path: Path,
) -> None:
    """_has_working_role returns False when no WORKING task for the role exists."""
    _write_task_status(tmp_path / "tasks", "CODER-001", "DONE", "CODER")
    assert _has_working_role(tmp_path, "coder") is False


def test_has_working_role_is_case_insensitive(tmp_path: Path) -> None:
    """_has_working_role matches role names case-insensitively."""
    _write_task_status(tmp_path / "tasks", "CODER-001", "WORKING", "coder")
    assert _has_working_role(tmp_path, "CODER") is True


def test_has_working_role_returns_false_when_tasks_dir_absent(
    tmp_path: Path,
) -> None:
    """_has_working_role returns False when the tasks directory does not exist."""
    assert _has_working_role(tmp_path, "coder") is False


def test_has_pending_role_returns_true_for_backlog_task(tmp_path: Path) -> None:
    """_has_pending_role returns True when a BACKLOG task for the role exists."""
    _write_task_status(tmp_path / "tasks", "PM-001", "BACKLOG", "PM")
    assert _has_pending_role(tmp_path, "pm") is True


def test_has_pending_role_returns_true_for_waiting_task(tmp_path: Path) -> None:
    """_has_pending_role returns True when a WAITING task for the role exists."""
    _write_task_status(tmp_path / "tasks", "CODER-001", "WAITING", "CODER")
    assert _has_pending_role(tmp_path, "coder") is True


def test_has_pending_role_returns_false_when_all_tasks_done(tmp_path: Path) -> None:
    """_has_pending_role returns False when all tasks for the role are DONE."""
    _write_task_status(tmp_path / "tasks", "CODER-001", "DONE", "CODER")
    _write_task_status(tmp_path / "tasks", "CODER-002", "WONT-DO", "CODER")
    assert _has_pending_role(tmp_path, "coder") is False


def test_has_in_flight_pm_returns_true_when_pm_task_is_working(
    tmp_path: Path,
) -> None:
    """_has_in_flight_pm returns True when a PM task is in WORKING state."""
    _write_task_status(tmp_path / "tasks", "PM-001", "WORKING", "PM")
    assert _has_in_flight_pm(tmp_path) is True


def test_has_in_flight_pm_returns_true_when_pm_task_is_backlog(
    tmp_path: Path,
) -> None:
    """_has_in_flight_pm returns True when a PM task is in BACKLOG state."""
    _write_task_status(tmp_path / "tasks", "PM-001", "BACKLOG", "PM")
    assert _has_in_flight_pm(tmp_path) is True


def test_has_in_flight_pm_returns_false_when_no_pm_tasks_in_flight(
    tmp_path: Path,
) -> None:
    """_has_in_flight_pm returns False when all PM tasks are in terminal states."""
    _write_task_status(tmp_path / "tasks", "PM-001", "DONE", "PM")
    assert _has_in_flight_pm(tmp_path) is False


# ---------------------------------------------------------------------------
# halt_after/drain — check_drain
# ---------------------------------------------------------------------------


def _make_project_root_with_release_state(
    tmp_path: Path,
    active_rc: str = "none",
    last_released: str = "none",
) -> Path:
    """Create a minimal project root with a release-state.md."""
    root = tmp_path / "project"
    root.mkdir()
    lines = ["# Release State\n\n## Active RC\n", f"{active_rc}\n"]
    if last_released and last_released != "none":
        lines.append(f"\n## Last Released\n{last_released}\n")
    (root / "release-state.md").write_text("".join(lines), encoding="utf-8")
    (root / "tasks").mkdir()
    return root


def test_check_drain_versioned_rc_returns_false_when_not_yet_released(
    tmp_path: Path,
) -> None:
    """check_drain returns False for rc:v1.0.0 when Last Released is absent."""
    root = _make_project_root_with_release_state(tmp_path, active_rc="rc/v1.0.0")
    result = check_drain("rc:v1.0.0", root)
    assert result is False


def test_check_drain_versioned_rc_returns_true_when_released_ge_captured(
    tmp_path: Path,
) -> None:
    """check_drain returns True for rc:v1.0.0 when Last Released is v1.0.0."""
    root = _make_project_root_with_release_state(
        tmp_path, active_rc="none", last_released="v1.0.0"
    )
    result = check_drain("rc:v1.0.0", root)
    assert result is True


def test_check_drain_versioned_rc_returns_true_when_released_exceeds_captured(
    tmp_path: Path,
) -> None:
    """check_drain returns True for rc:v1.0.0 when Last Released is v1.1.0 (higher)."""
    root = _make_project_root_with_release_state(
        tmp_path, active_rc="none", last_released="v1.1.0"
    )
    result = check_drain("rc:v1.0.0", root)
    assert result is True


def test_check_drain_versioned_rc_returns_false_when_released_below_captured(
    tmp_path: Path,
) -> None:
    """check_drain returns False for rc:v1.1.0 when Last Released is v1.0.0 (lower)."""
    root = _make_project_root_with_release_state(
        tmp_path, active_rc="none", last_released="v1.0.0"
    )
    result = check_drain("rc:v1.1.0", root)
    assert result is False


def test_check_drain_bare_rc_promotes_immediately_when_no_active_rc(
    tmp_path: Path,
) -> None:
    """check_drain (bare 'rc') promotes to HALT immediately when Active RC is 'none' (an earlier defect)."""
    root = _make_project_root_with_release_state(tmp_path, active_rc="none")
    (root / "HALT-AFTER").write_text("rc\n", encoding="utf-8")
    result = check_drain("rc", root)
    # an earlier defect: should promote immediately and return True
    assert result is True
    assert (root / "HALT").exists()


def test_check_drain_bare_rc_rewrites_halt_after_when_active_rc_present(
    tmp_path: Path,
) -> None:
    """check_drain (bare 'rc') rewrites HALT-AFTER with captured version when RC is in flight."""
    root = _make_project_root_with_release_state(tmp_path, active_rc="rc/v0.55.0")
    (root / "HALT-AFTER").write_text("rc\n", encoding="utf-8")
    result = check_drain("rc", root)
    # Not yet drained — RC is in flight
    assert result is False
    # HALT-AFTER should now contain the versioned token
    content = (root / "HALT-AFTER").read_text(encoding="utf-8").strip()
    assert content.startswith("rc:")
    assert "v0.55.0" in content


def test_check_drain_coder_returns_true_when_no_pending_or_working_tasks(
    tmp_path: Path,
) -> None:
    """check_drain returns True for 'coder' when no CODER tasks are pending or working."""
    root = _make_project_root_with_release_state(tmp_path)
    # Add a DONE CODER task — should not block drain
    _write_task_status(root / "tasks", "CODER-001", "DONE", "CODER")
    result = check_drain("coder", root)
    assert result is True


def test_check_drain_coder_returns_false_when_working_coder_task_exists(
    tmp_path: Path,
) -> None:
    """check_drain returns False for 'coder' when a CODER task is WORKING."""
    root = _make_project_root_with_release_state(tmp_path)
    _write_task_status(root / "tasks", "CODER-001", "WORKING", "CODER")
    result = check_drain("coder", root)
    assert result is False


def test_check_drain_coder_returns_false_when_pm_task_in_flight(
    tmp_path: Path,
) -> None:
    """check_drain returns False for 'coder' when a PM task is still in flight."""
    root = _make_project_root_with_release_state(tmp_path)
    # No CODER tasks at all, but PM is BACKLOG (may materialize CODER tasks)
    _write_task_status(root / "tasks", "PM-001", "BACKLOG", "PM")
    result = check_drain("coder", root)
    assert result is False


def test_check_drain_pm_returns_true_when_no_pm_tasks_pending(
    tmp_path: Path,
) -> None:
    """check_drain returns True for 'pm' when no PM tasks are pending or working."""
    root = _make_project_root_with_release_state(tmp_path)
    _write_task_status(root / "tasks", "PM-001", "DONE", "PM")
    result = check_drain("pm", root)
    assert result is True


def test_check_drain_raises_value_error_for_unsupported_token(
    tmp_path: Path,
) -> None:
    """check_drain raises ValueError for an unsupported event token."""
    root = _make_project_root_with_release_state(tmp_path)
    with pytest.raises(ValueError, match="unsupported event token"):
        check_drain("deploy", root)
