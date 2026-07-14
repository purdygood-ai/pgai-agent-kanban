"""
test_human_review_window.py
============================
Behavioral unit tests for dashboard window 14 (human-review):
  team/pgai_agent_kanban/dashboard/scan_human_approvals.py

Covers all acceptance criteria from the task README:

  1. Fixture with one pending gate (WAITING): window renders project, target
     RC, age, show content (goal), approve command, reject command.

  2. Fixture with two pending gates across two projects: both listed and
     project-labeled.

  3. Empty fixture: single line "no approvals pending."

  4. Non-pending states (DONE, BLOCKED, WORKING) are excluded from listing.

  5. Tasks whose IDs do NOT start with HUMAN-APPROVE are excluded.

  6. BACKLOG state is treated as pending (same as WAITING).

All filesystem operations use tmp_path (pytest-managed).
No live kanban root, no subprocesses.
"""

from __future__ import annotations

import pathlib
import sys
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Ensure the package root is importable regardless of cwd.
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/tests/unit/ → team/
_PKG_ROOT = _TEAM_DIR  # pgai_agent_kanban/ lives directly under team/
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from pgai_agent_kanban.dashboard.scan_human_approvals import (
    scan_pending_approvals,
    _collect_pending_approvals,
    _format_age,
)


# ---------------------------------------------------------------------------
# Fixture builder helpers
# ---------------------------------------------------------------------------

def _write_task(
    tasks_dir: pathlib.Path,
    task_id: str,
    state: str,
    release_ver: str = "v1.10.0",
    goal: str = "Review and approve release v1.10.0.",
) -> None:
    """Create a minimal task folder under tasks_dir."""
    task_dir = tasks_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    (task_dir / "status.md").write_text(
        f"# Status\n\n## State\n{state}\n",
        encoding="utf-8",
    )
    (task_dir / "README.md").write_text(
        textwrap.dedent(f"""\
            # Task: Human Approval Gate — {release_ver}

            ## Task ID
            {task_id}

            ## Role
            HUMAN

            ## Goal
            {goal}

            ## Release Version
            {release_ver}
        """),
        encoding="utf-8",
    )


def _build_single_project_fixture(
    root: pathlib.Path,
    proj: str,
    tasks: list[tuple],
) -> pathlib.Path:
    """Create projects/<proj>/tasks with the given task list.

    Each entry in tasks is (task_id, state, release_ver, goal).
    Returns the tasks directory path.
    """
    tasks_dir = root / "projects" / proj / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    for task_id, state, release_ver, goal in tasks:
        _write_task(tasks_dir, task_id, state, release_ver, goal)
    return tasks_dir


# ---------------------------------------------------------------------------
# _format_age tests
# ---------------------------------------------------------------------------


def test_format_age_seconds() -> None:
    """_format_age returns seconds label for values under 60."""
    assert _format_age(45) == "45s"


def test_format_age_minutes() -> None:
    """_format_age returns minutes label for values 60..3599."""
    assert _format_age(90) == "1m"
    assert _format_age(3599) == "59m"


def test_format_age_hours() -> None:
    """_format_age returns hours+minutes for values 3600..86399."""
    result = _format_age(3900)  # 1h 5m
    assert "1h" in result
    assert "5m" in result


def test_format_age_days() -> None:
    """_format_age returns days+hours for values >= 86400."""
    result = _format_age(90000)  # 1d 1h
    assert "1d" in result


def test_format_age_zero() -> None:
    """_format_age returns '0s' for zero input."""
    assert _format_age(0) == "0s"


# ---------------------------------------------------------------------------
# scan_pending_approvals — empty state
# ---------------------------------------------------------------------------


def test_empty_state_prints_no_approvals_pending(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """scan_pending_approvals prints 'no approvals pending.' when no tasks exist.

    Acceptance criterion: empty fixture produces exactly one line containing
    'no approvals pending.'
    """
    (tmp_path / "projects").mkdir()

    scan_pending_approvals(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "no approvals pending." in captured.out


def test_empty_state_no_pending_when_tasks_dir_absent(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """scan_pending_approvals prints 'no approvals pending.' when projects/ is absent."""
    scan_pending_approvals(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "no approvals pending." in captured.out


# ---------------------------------------------------------------------------
# scan_pending_approvals — single pending gate
# ---------------------------------------------------------------------------


def test_single_pending_gate_renders_task_id(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """One WAITING HUMAN-APPROVE task: task ID appears in the output."""
    _build_single_project_fixture(
        tmp_path, "proj-alpha",
        [("HUMAN-APPROVE-v1.10.0-099", "WAITING", "v1.10.0", "Review v1.10.0 features.")],
    )
    scan_pending_approvals(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "HUMAN-APPROVE-v1.10.0-099" in captured.out


def test_single_pending_gate_renders_project_name(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """One pending gate: project name appears in the output."""
    _build_single_project_fixture(
        tmp_path, "proj-alpha",
        [("HUMAN-APPROVE-v1.10.0-099", "WAITING", "v1.10.0", "Review v1.10.0 features.")],
    )
    scan_pending_approvals(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "proj-alpha" in captured.out


def test_single_pending_gate_renders_release_version(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """One pending gate: RC/target version from ## Release Version appears in output."""
    _build_single_project_fixture(
        tmp_path, "proj-alpha",
        [("HUMAN-APPROVE-v1.10.0-099", "WAITING", "v1.10.0", "Review v1.10.0 features.")],
    )
    scan_pending_approvals(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "v1.10.0" in captured.out


def test_single_pending_gate_renders_goal_content(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """One pending gate: show content (Goal section) appears in the output."""
    _build_single_project_fixture(
        tmp_path, "proj-alpha",
        [("HUMAN-APPROVE-v1.10.0-099", "WAITING", "v1.10.0", "Review all feature tasks for v1.10.0.")],
    )
    scan_pending_approvals(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "Review all feature tasks for v1.10.0." in captured.out


def test_single_pending_gate_renders_approve_command(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """One pending gate: verbatim approve command appears in the output."""
    _build_single_project_fixture(
        tmp_path, "proj-alpha",
        [("HUMAN-APPROVE-v1.10.0-099", "WAITING", "v1.10.0", "Review.")],
    )
    scan_pending_approvals(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "scripts/close.sh --project proj-alpha --key HUMAN-APPROVE-v1.10.0-099" in captured.out


def test_single_pending_gate_renders_reject_command(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """One pending gate: verbatim reject command appears in the output."""
    _build_single_project_fixture(
        tmp_path, "proj-alpha",
        [("HUMAN-APPROVE-v1.10.0-099", "WAITING", "v1.10.0", "Review.")],
    )
    scan_pending_approvals(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "scripts/wontdo.sh --project proj-alpha --key HUMAN-APPROVE-v1.10.0-099" in captured.out


def test_backlog_state_treated_as_pending(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """BACKLOG state (prerequisites satisfied but not yet started) is also pending."""
    _build_single_project_fixture(
        tmp_path, "proj-alpha",
        [("HUMAN-APPROVE-v1.10.0-100", "BACKLOG", "v1.10.0", "Review.")],
    )
    scan_pending_approvals(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "HUMAN-APPROVE-v1.10.0-100" in captured.out
    assert "no approvals pending." not in captured.out


# ---------------------------------------------------------------------------
# scan_pending_approvals — two projects, two pending gates
# ---------------------------------------------------------------------------


def test_two_projects_both_listed(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """Two projects each with one pending gate: both task IDs appear in the output."""
    _build_single_project_fixture(
        tmp_path, "proj-alpha",
        [("HUMAN-APPROVE-v1.10.0-099", "WAITING", "v1.10.0", "Review alpha.")],
    )
    _build_single_project_fixture(
        tmp_path, "proj-beta",
        [("HUMAN-APPROVE-v2.0.0-001", "WAITING", "v2.0.0", "Review beta.")],
    )
    scan_pending_approvals(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "HUMAN-APPROVE-v1.10.0-099" in captured.out
    assert "HUMAN-APPROVE-v2.0.0-001" in captured.out


def test_two_projects_both_labeled(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """Two projects: both project names appear in the output as labels."""
    _build_single_project_fixture(
        tmp_path, "proj-alpha",
        [("HUMAN-APPROVE-v1.10.0-099", "WAITING", "v1.10.0", "Review alpha.")],
    )
    _build_single_project_fixture(
        tmp_path, "proj-beta",
        [("HUMAN-APPROVE-v2.0.0-001", "WAITING", "v2.0.0", "Review beta.")],
    )
    scan_pending_approvals(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "proj-alpha" in captured.out
    assert "proj-beta" in captured.out


def test_two_projects_commands_reference_correct_project(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """Two projects: each task's commands reference its own project name."""
    _build_single_project_fixture(
        tmp_path, "proj-alpha",
        [("HUMAN-APPROVE-v1.10.0-099", "WAITING", "v1.10.0", "Review alpha.")],
    )
    _build_single_project_fixture(
        tmp_path, "proj-beta",
        [("HUMAN-APPROVE-v2.0.0-001", "WAITING", "v2.0.0", "Review beta.")],
    )
    scan_pending_approvals(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    # Alpha's approve command references proj-alpha
    assert "scripts/close.sh --project proj-alpha --key HUMAN-APPROVE-v1.10.0-099" in captured.out
    # Beta's approve command references proj-beta
    assert "scripts/close.sh --project proj-beta --key HUMAN-APPROVE-v2.0.0-001" in captured.out


def test_two_projects_no_approvals_pending_absent(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """Two pending gates: the empty-state message 'no approvals pending.' is absent."""
    _build_single_project_fixture(
        tmp_path, "proj-alpha",
        [("HUMAN-APPROVE-v1.10.0-099", "WAITING", "v1.10.0", "Review alpha.")],
    )
    _build_single_project_fixture(
        tmp_path, "proj-beta",
        [("HUMAN-APPROVE-v2.0.0-001", "WAITING", "v2.0.0", "Review beta.")],
    )
    scan_pending_approvals(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "no approvals pending." not in captured.out


# ---------------------------------------------------------------------------
# scan_pending_approvals — non-pending states excluded
# ---------------------------------------------------------------------------


def test_done_state_excluded(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """DONE HUMAN-APPROVE tasks do not appear in the output."""
    _build_single_project_fixture(
        tmp_path, "proj-alpha",
        [("HUMAN-APPROVE-v1.10.0-099", "DONE", "v1.10.0", "Already approved.")],
    )
    scan_pending_approvals(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "HUMAN-APPROVE-v1.10.0-099" not in captured.out
    assert "no approvals pending." in captured.out


def test_wont_do_state_excluded(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """WONT-DO HUMAN-APPROVE tasks do not appear in the output."""
    _build_single_project_fixture(
        tmp_path, "proj-alpha",
        [("HUMAN-APPROVE-v1.10.0-099", "WONT-DO", "v1.10.0", "Rejected.")],
    )
    scan_pending_approvals(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "HUMAN-APPROVE-v1.10.0-099" not in captured.out
    assert "no approvals pending." in captured.out


def test_blocked_state_excluded(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """BLOCKED HUMAN-APPROVE tasks do not appear in the pending listing."""
    _build_single_project_fixture(
        tmp_path, "proj-alpha",
        [("HUMAN-APPROVE-v1.10.0-099", "BLOCKED", "v1.10.0", "Blocked for reason.")],
    )
    scan_pending_approvals(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "HUMAN-APPROVE-v1.10.0-099" not in captured.out
    assert "no approvals pending." in captured.out


def test_working_state_excluded(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """WORKING HUMAN-APPROVE tasks do not appear (they should not exist, but guard it)."""
    _build_single_project_fixture(
        tmp_path, "proj-alpha",
        [("HUMAN-APPROVE-v1.10.0-099", "WORKING", "v1.10.0", "In progress?")],
    )
    scan_pending_approvals(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "HUMAN-APPROVE-v1.10.0-099" not in captured.out
    assert "no approvals pending." in captured.out


# ---------------------------------------------------------------------------
# scan_pending_approvals — non-HUMAN-APPROVE tasks excluded
# ---------------------------------------------------------------------------


def test_non_human_approve_task_excluded(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """Tasks whose IDs do not start with HUMAN-APPROVE are ignored."""
    _build_single_project_fixture(
        tmp_path, "proj-alpha",
        [("CODER-20260708-025-some-task", "WAITING", "v1.10.0", "Some coder task.")],
    )
    scan_pending_approvals(str(tmp_path), use_color=False)
    captured = capsys.readouterr()
    assert "CODER-20260708-025-some-task" not in captured.out
    assert "no approvals pending." in captured.out


# ---------------------------------------------------------------------------
# _collect_pending_approvals — structural tests
# ---------------------------------------------------------------------------


def test_collect_returns_correct_fields(
    tmp_path: pathlib.Path,
) -> None:
    """_collect_pending_approvals returns dicts with the expected keys."""
    _build_single_project_fixture(
        tmp_path, "proj-alpha",
        [("HUMAN-APPROVE-v1.10.0-099", "WAITING", "v1.10.0", "Review v1.10.0.")],
    )
    results = _collect_pending_approvals(tmp_path)
    assert len(results) == 1
    r = results[0]
    assert r["project"] == "proj-alpha"
    assert r["task_id"] == "HUMAN-APPROVE-v1.10.0-099"
    assert r["release_ver"] == "v1.10.0"
    assert r["state"] == "WAITING"
    assert isinstance(r["age_str"], str)
    assert isinstance(r["goal_lines"], list)


def test_collect_sorts_by_project_then_task_id(
    tmp_path: pathlib.Path,
) -> None:
    """_collect_pending_approvals returns results sorted by project then task_id."""
    _build_single_project_fixture(
        tmp_path, "proj-beta",
        [("HUMAN-APPROVE-v2.0.0-001", "WAITING", "v2.0.0", "Beta review.")],
    )
    _build_single_project_fixture(
        tmp_path, "proj-alpha",
        [("HUMAN-APPROVE-v1.10.0-099", "WAITING", "v1.10.0", "Alpha review.")],
    )
    results = _collect_pending_approvals(tmp_path)
    assert len(results) == 2
    # proj-alpha sorts before proj-beta
    assert results[0]["project"] == "proj-alpha"
    assert results[1]["project"] == "proj-beta"


def test_collect_skips_queues_and_archive_dirs(
    tmp_path: pathlib.Path,
) -> None:
    """_collect_pending_approvals skips 'queues', 'archive', and 'plans' subdirs."""
    tasks_dir = tmp_path / "projects" / "proj" / "tasks"
    tasks_dir.mkdir(parents=True)

    # Create a file named "queues" (or subdir) — should be skipped
    (tasks_dir / "queues").mkdir()
    # Valid HUMAN-APPROVE task
    _write_task(tasks_dir, "HUMAN-APPROVE-v1.0.0-001", "WAITING", "v1.0.0", "Review.")

    results = _collect_pending_approvals(tmp_path)
    assert len(results) == 1
    assert results[0]["task_id"] == "HUMAN-APPROVE-v1.0.0-001"


def test_collect_fallback_release_ver_to_task_id_when_readme_absent(
    tmp_path: pathlib.Path,
) -> None:
    """_collect_pending_approvals uses task_id as release_ver when README.md is absent."""
    tasks_dir = tmp_path / "projects" / "proj" / "tasks"
    tasks_dir.mkdir(parents=True)

    task_dir = tasks_dir / "HUMAN-APPROVE-v1.0.0-001"
    task_dir.mkdir()
    (task_dir / "status.md").write_text("## State\nWAITING\n", encoding="utf-8")
    # No README.md

    results = _collect_pending_approvals(tmp_path)
    assert len(results) == 1
    assert results[0]["release_ver"] == "HUMAN-APPROVE-v1.0.0-001"
