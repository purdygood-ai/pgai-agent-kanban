"""
test_task_ids_bash.py
=====================
Behavioral unit tests for team/scripts/lib/task_ids.sh.

Tests source the shell script and invoke kanban_* functions via the bash
harness.  Synthetic task directory trees are created under tmp_path.

Functions under test:
  - kanban_parse_task_id: parse ID string into component variables
  - kanban_task_agent:    extract agent field
  - kanban_task_date:     extract date field
  - kanban_task_seq:      extract sequence field
  - kanban_task_slug:     extract slug field
  - kanban_task_participant: always returns 'claude'
  - kanban_next_task_seq: scan tasks/ and return next sequence number
  - kanban_task_id:       full ID composition (calls next_task_seq internally)
"""

from __future__ import annotations

import pathlib

import pytest

from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/task_ids.sh"


def _source(func_call: str) -> str:
    """Return a bash snippet that sources task_ids.sh then calls func_call."""
    return f"source {_LIB} && {func_call}"


def _make_task_dirs(tasks_dir: pathlib.Path, *names: str) -> None:
    """Create named subdirectories under tasks_dir to simulate existing tasks."""
    tasks_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (tasks_dir / name).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# kanban_parse_task_id — populate _TASK_* variables
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command, expected",
    [
        (
            "kanban_parse_task_id CODER-20260518-002-implement-feature && echo $_TASK_AGENT",
            "CODER",
        ),  # _TASK_AGENT populated
        (
            "kanban_parse_task_id CODER-20260518-002-implement-feature && echo $_TASK_DATE",
            "20260518",
        ),  # _TASK_DATE populated
        (
            "kanban_parse_task_id CODER-20260518-002-implement-feature && echo $_TASK_SEQ",
            "002",
        ),  # _TASK_SEQ populated
        (
            "kanban_parse_task_id CODER-20260518-002-implement-feature && echo $_TASK_SLUG",
            "implement-feature",
        ),  # _TASK_SLUG populated
        (
            "kanban_parse_task_id PM-20260628-001-decompose-v0-102-0 && echo $_TASK_SLUG",
            "decompose-v0-102-0",
        ),  # multi-word slug including hyphens
        ("kanban_task_agent WRITER-20260601-005-write-readme", "WRITER"),
        ("kanban_task_date WRITER-20260601-005-write-readme", "20260601"),
        ("kanban_task_seq WRITER-20260601-005-write-readme", "005"),
        ("kanban_task_slug WRITER-20260601-005-write-readme", "write-readme"),
        (
            "kanban_task_participant CODER-20260518-001-anything",
            "claude",
        ),  # always echoes 'claude' regardless of the ID
        ("kanban_task_agent invalid-id-here", ""),  # unrecognised ID format
    ],
)
def test_task_id_accessor_behaviors(tmp_path: pathlib.Path, command: str, expected: str) -> None:
    """kanban_parse_task_id and the kanban_task_* accessors extract agent/date/seq/slug/participant fields."""
    result = run_bash(tmp_path, _source(command))
    assert result.returncode == 0
    assert result.stdout.strip() == expected


def test_parse_task_id_returns_nonzero_for_invalid_id(tmp_path: pathlib.Path) -> None:
    """kanban_parse_task_id returns non-zero for a string that does not match the format."""
    result = run_bash(
        tmp_path,
        _source("kanban_parse_task_id not-a-valid-id"),
    )
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# kanban_next_task_seq — find the next available sequence number
# ---------------------------------------------------------------------------


def test_next_task_seq_returns_001_when_no_existing_tasks(tmp_path: pathlib.Path) -> None:
    """kanban_next_task_seq returns 001 when no matching task directories exist."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    result = run_bash(
        tmp_path,
        _source(f"kanban_next_task_seq '{tasks_dir}' CODER 20260518"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "001"


def test_next_task_seq_returns_001_when_tasks_dir_absent(tmp_path: pathlib.Path) -> None:
    """kanban_next_task_seq returns 001 when the tasks directory does not exist."""
    absent_dir = tmp_path / "nonexistent"
    result = run_bash(
        tmp_path,
        _source(f"kanban_next_task_seq '{absent_dir}' CODER 20260518"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "001"


@pytest.mark.parametrize(
    "existing_dir_1, existing_dir_2, query_role, query_date, expected",
    [
        (
            "CODER-20260518-001-fix-something",
            "CODER-20260518-002-add-feature",
            "CODER",
            "20260518",
            "003",
        ),  # matching role+date -> max+1
        (
            "WRITER-20260518-001-docs",
            "WRITER-20260518-002-more-docs",
            "CODER",
            "20260518",
            "001",
        ),  # different role ignored
        (
            "CODER-20260517-001-yesterday",
            "CODER-20260517-002-also-yesterday",
            "CODER",
            "20260518",
            "001",
        ),  # different date ignored
    ],
)
def test_next_task_seq_scoped_to_role_and_date(
    tmp_path: pathlib.Path,
    existing_dir_1: str,
    existing_dir_2: str,
    query_role: str,
    query_date: str,
    expected: str,
) -> None:
    """kanban_next_task_seq only counts task dirs matching both the queried role and date."""
    tasks_dir = tmp_path / "tasks"
    _make_task_dirs(tasks_dir, existing_dir_1, existing_dir_2)
    result = run_bash(
        tmp_path,
        _source(f"kanban_next_task_seq '{tasks_dir}' {query_role} {query_date}"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == expected


def test_next_task_seq_zero_pads_to_three_digits(tmp_path: pathlib.Path) -> None:
    """kanban_next_task_seq always produces a zero-padded three-digit sequence."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    result = run_bash(
        tmp_path,
        _source(f"kanban_next_task_seq '{tasks_dir}' PM 20260518"),
    )
    assert result.returncode == 0
    seq = result.stdout.strip()
    assert len(seq) == 3, f"Expected 3-char zero-padded sequence; got {seq!r}"
    assert seq == "001"


# ---------------------------------------------------------------------------
# kanban_task_id — full ID composition
# ---------------------------------------------------------------------------


def test_task_id_produces_correct_format(tmp_path: pathlib.Path) -> None:
    """kanban_task_id produces an ID in ROLE-DATE-NNN-SLUG format."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    result = run_bash(
        tmp_path,
        _source(f"kanban_task_id '{tasks_dir}' CODER 20260518 fix-something"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "CODER-20260518-001-fix-something"


def test_task_id_increments_sequence_based_on_existing_dirs(tmp_path: pathlib.Path) -> None:
    """kanban_task_id increments past the highest existing sequence for the role/date."""
    tasks_dir = tmp_path / "tasks"
    _make_task_dirs(tasks_dir, "CODER-20260518-003-prior-task")
    result = run_bash(
        tmp_path,
        _source(f"kanban_task_id '{tasks_dir}' CODER 20260518 new-task"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "CODER-20260518-004-new-task"
