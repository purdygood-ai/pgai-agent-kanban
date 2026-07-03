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


def test_parse_task_id_sets_agent_field(tmp_path: pathlib.Path) -> None:
    """kanban_parse_task_id populates _TASK_AGENT from the ID string."""
    result = run_bash(
        tmp_path,
        _source(
            "kanban_parse_task_id CODER-20260518-002-implement-feature && echo $_TASK_AGENT"
        ),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "CODER"


def test_parse_task_id_sets_date_field(tmp_path: pathlib.Path) -> None:
    """kanban_parse_task_id populates _TASK_DATE from the ID string."""
    result = run_bash(
        tmp_path,
        _source(
            "kanban_parse_task_id CODER-20260518-002-implement-feature && echo $_TASK_DATE"
        ),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "20260518"


def test_parse_task_id_sets_seq_field(tmp_path: pathlib.Path) -> None:
    """kanban_parse_task_id populates _TASK_SEQ from the ID string."""
    result = run_bash(
        tmp_path,
        _source(
            "kanban_parse_task_id CODER-20260518-002-implement-feature && echo $_TASK_SEQ"
        ),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "002"


def test_parse_task_id_sets_slug_field(tmp_path: pathlib.Path) -> None:
    """kanban_parse_task_id populates _TASK_SLUG from the ID string."""
    result = run_bash(
        tmp_path,
        _source(
            "kanban_parse_task_id CODER-20260518-002-implement-feature && echo $_TASK_SLUG"
        ),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "implement-feature"


def test_parse_task_id_returns_nonzero_for_invalid_id(tmp_path: pathlib.Path) -> None:
    """kanban_parse_task_id returns non-zero for a string that does not match the format."""
    result = run_bash(
        tmp_path,
        _source("kanban_parse_task_id not-a-valid-id"),
    )
    assert result.returncode != 0


def test_parse_task_id_handles_multi_word_slug(tmp_path: pathlib.Path) -> None:
    """kanban_parse_task_id extracts the entire slug including hyphens."""
    result = run_bash(
        tmp_path,
        _source(
            "kanban_parse_task_id PM-20260628-001-decompose-v0-102-0 && echo $_TASK_SLUG"
        ),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "decompose-v0-102-0"


# ---------------------------------------------------------------------------
# Accessor functions (kanban_task_agent, _date, _seq, _slug, _participant)
# ---------------------------------------------------------------------------


def test_task_agent_echoes_agent_field(tmp_path: pathlib.Path) -> None:
    """kanban_task_agent echoes the agent component of the ID."""
    result = run_bash(
        tmp_path,
        _source("kanban_task_agent WRITER-20260601-005-write-readme"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "WRITER"


def test_task_date_echoes_date_field(tmp_path: pathlib.Path) -> None:
    """kanban_task_date echoes the 8-digit date component."""
    result = run_bash(
        tmp_path,
        _source("kanban_task_date WRITER-20260601-005-write-readme"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "20260601"


def test_task_seq_echoes_sequence_field(tmp_path: pathlib.Path) -> None:
    """kanban_task_seq echoes the zero-padded sequence number."""
    result = run_bash(
        tmp_path,
        _source("kanban_task_seq WRITER-20260601-005-write-readme"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "005"


def test_task_slug_echoes_slug_field(tmp_path: pathlib.Path) -> None:
    """kanban_task_slug echoes the kebab-case slug."""
    result = run_bash(
        tmp_path,
        _source("kanban_task_slug WRITER-20260601-005-write-readme"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "write-readme"


def test_task_participant_always_echoes_claude(tmp_path: pathlib.Path) -> None:
    """kanban_task_participant always echoes 'claude' regardless of the ID."""
    result = run_bash(
        tmp_path,
        _source("kanban_task_participant CODER-20260518-001-anything"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "claude"


def test_task_agent_returns_empty_for_invalid_id(tmp_path: pathlib.Path) -> None:
    """kanban_task_agent echoes an empty string for an unrecognised ID format."""
    result = run_bash(
        tmp_path,
        _source("kanban_task_agent invalid-id-here"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


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


def test_next_task_seq_returns_next_after_existing(tmp_path: pathlib.Path) -> None:
    """kanban_next_task_seq returns max+1 when matching task dirs exist."""
    tasks_dir = tmp_path / "tasks"
    _make_task_dirs(
        tasks_dir,
        "CODER-20260518-001-fix-something",
        "CODER-20260518-002-add-feature",
    )
    result = run_bash(
        tmp_path,
        _source(f"kanban_next_task_seq '{tasks_dir}' CODER 20260518"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "003"


def test_next_task_seq_ignores_different_role(tmp_path: pathlib.Path) -> None:
    """kanban_next_task_seq ignores task dirs for a different role."""
    tasks_dir = tmp_path / "tasks"
    _make_task_dirs(
        tasks_dir,
        "WRITER-20260518-001-docs",
        "WRITER-20260518-002-more-docs",
    )
    result = run_bash(
        tmp_path,
        _source(f"kanban_next_task_seq '{tasks_dir}' CODER 20260518"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "001"


def test_next_task_seq_ignores_different_date(tmp_path: pathlib.Path) -> None:
    """kanban_next_task_seq ignores task dirs for a different date stamp."""
    tasks_dir = tmp_path / "tasks"
    _make_task_dirs(
        tasks_dir,
        "CODER-20260517-001-yesterday",
        "CODER-20260517-002-also-yesterday",
    )
    result = run_bash(
        tmp_path,
        _source(f"kanban_next_task_seq '{tasks_dir}' CODER 20260518"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "001"


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
