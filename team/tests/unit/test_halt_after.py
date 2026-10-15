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


@pytest.mark.parametrize(
    "content, expected",
    [
        ("", "rc"),  # empty content defaults to 'rc'
        ("   \n\t  ", "rc"),  # whitespace-only content defaults to 'rc'
        ("RC\n", "rc"),  # uppercase normalized to lowercase
        ("  coder  \n", "coder"),  # surrounding whitespace stripped
        ("pm", "pm"),
        ("coder", "coder"),
        ("writer", "writer"),
        ("tester", "tester"),
        ("cm", "cm"),
        ("rc:v0.40.0", "rc:v0.40.0"),  # versioned rc token
    ],
)
def test_parse_token_returns_supported_token(content: str, expected: str) -> None:
    """parse_token classifies supported content into its normalized token."""
    assert parse_token(content) == expected


def test_parse_token_handles_versioned_rc_with_uppercase_rc_prefix() -> None:
    """parse_token lowercases the 'RC:' prefix for versioned rc tokens."""
    result = parse_token("RC:v1.2.3")
    assert result is not None
    assert result.startswith("rc:")


@pytest.mark.parametrize(
    "content",
    [
        "deploy",  # not in the supported set
        "stop-everything",  # arbitrary unrecognized text
        "coder2",  # partial match on a supported token
    ],
)
def test_parse_token_returns_none_for_unrecognized_content(content: str) -> None:
    """parse_token returns None for tokens not in the supported set."""
    assert parse_token(content) is None


# ---------------------------------------------------------------------------
# halt_after/promote — _event_drained_note and _build_audit_entry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token, expected_substring",
    [
        ("rc", "Active RC cleared"),  # bare 'rc' -> 'Active RC cleared' note
        ("rc:v0.42.0", "v0.42.0"),  # versioned rc token includes the version
        ("coder", "CODER"),  # coder event mentions 'CODER tasks'
    ],
)
def test_event_drained_note(token: str, expected_substring: str) -> None:
    """_event_drained_note describes the resolved drain condition per token."""
    note = _event_drained_note(token)
    assert expected_substring in note


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


@pytest.mark.parametrize(
    "task_state, written_role, queried_role, expected",
    [
        ("WORKING", "CODER", "coder", True),  # WORKING task for the role exists
        ("DONE", "CODER", "coder", False),  # no WORKING task for the role
        ("WORKING", "coder", "CODER", True),  # role match is case-insensitive
    ],
)
def test_has_working_role(
    tmp_path: Path, task_state: str, written_role: str, queried_role: str, expected: bool
) -> None:
    """_has_working_role detects a WORKING task for the role, case-insensitively."""
    _write_task_status(tmp_path / "tasks", "CODER-001", task_state, written_role)
    assert _has_working_role(tmp_path, queried_role) is expected


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


@pytest.mark.parametrize(
    "pm_task_state, expected",
    [
        ("WORKING", True),
        ("BACKLOG", True),
        ("DONE", False),  # terminal state -> not in flight
    ],
)
def test_has_in_flight_pm(tmp_path: Path, pm_task_state: str, expected: bool) -> None:
    """_has_in_flight_pm is True while a PM task is WORKING or BACKLOG, False once terminal."""
    _write_task_status(tmp_path / "tasks", "PM-001", pm_task_state, "PM")
    assert _has_in_flight_pm(tmp_path) is expected


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


@pytest.mark.parametrize(
    "captured_token, last_released, expected",
    [
        ("rc:v1.0.0", "v1.0.0", True),  # released == captured
        ("rc:v1.0.0", "v1.1.0", True),  # released exceeds captured
        ("rc:v1.1.0", "v1.0.0", False),  # released below captured
    ],
)
def test_check_drain_versioned_rc(
    tmp_path: Path, captured_token: str, last_released: str, expected: bool
) -> None:
    """check_drain for a versioned rc token compares Last Released against the captured version with >=."""
    root = _make_project_root_with_release_state(
        tmp_path, active_rc="none", last_released=last_released
    )
    result = check_drain(captured_token, root)
    assert result is expected


def test_check_drain_bare_rc_reports_drained_when_no_active_rc(
    tmp_path: Path,
) -> None:
    """check_drain (bare 'rc') reports drained when Active RC is 'none' (an earlier defect fixed).

    check_drain only REPORTS the condition; it no longer promotes itself.
    Promotion is the RC-close gate's job (rc_gate.evaluate) so there is
    exactly one call site that ever invokes promote() -- see
    team/pgai_agent_kanban/halt_after/rc_gate.py. Prior to that module's
    introduction, this branch called promote() directly; that behavior
    moved to rc_gate.evaluate() (see test_halt_after_rc_gate.py), which is
    exercised end-to-end in test_halt_after_rc_close_gate.py's fixtures.
    """
    root = _make_project_root_with_release_state(tmp_path, active_rc="none")
    (root / "HALT-AFTER").write_text("rc\n", encoding="utf-8")
    result = check_drain("rc", root)
    assert result is True
    assert not (root / "HALT").exists(), "check_drain must not promote by itself"


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


@pytest.mark.parametrize(
    "seed_task_id, seed_state, seed_role, token, expected",
    [
        # DONE CODER task should not block the 'coder' drain
        ("CODER-001", "DONE", "CODER", "coder", True),
        # a WORKING CODER task blocks the 'coder' drain
        ("CODER-001", "WORKING", "CODER", "coder", False),
        # no CODER tasks at all, but PM is BACKLOG (may still materialize CODER tasks)
        ("PM-001", "BACKLOG", "PM", "coder", False),
        # DONE PM task should not block the 'pm' drain
        ("PM-001", "DONE", "PM", "pm", True),
    ],
)
def test_check_drain_role_token(
    tmp_path: Path,
    seed_task_id: str,
    seed_state: str,
    seed_role: str,
    token: str,
    expected: bool,
) -> None:
    """check_drain for a role token (coder/pm) reflects whether the role has in-flight work."""
    root = _make_project_root_with_release_state(tmp_path)
    _write_task_status(root / "tasks", seed_task_id, seed_state, seed_role)
    result = check_drain(token, root)
    assert result is expected


def test_check_drain_raises_value_error_for_unsupported_token(
    tmp_path: Path,
) -> None:
    """check_drain raises ValueError for an unsupported event token."""
    root = _make_project_root_with_release_state(tmp_path)
    with pytest.raises(ValueError, match="unsupported event token"):
        check_drain("deploy", root)
