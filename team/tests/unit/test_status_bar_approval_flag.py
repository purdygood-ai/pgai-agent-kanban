"""
test_status_bar_approval_flag.py
=================================
Behavioral tests for the APPROVAL(n) flag in the tmux status-line scripts:
  - team/scripts/dashboard/status-right.sh
  - team/scripts/dashboard/status-bottom.sh

Covers all acceptance criteria from CODER-20260708-024:

  1. One pending HUMAN-APPROVE task (WAITING) → status line contains
     '✋ APPROVAL(1)' (USE_COLOR) or '[APPROVAL(1)]' (NO_COLOR / TERM=dumb).

  2. Two pending HUMAN-APPROVE tasks across two projects → 'APPROVAL(2)'.

  3. No pending tasks (n=0) → indicator string absent from status-line output.

  4. Approve-clears-flag behavioral fixture:
       inject → flag present → approve (set State to DONE) → next render
       omits the flag.
     This is the load-bearing half: a stale ✋ after approval would train
     operators to ignore it.

Fixture structure built by each test (under pytest tmp_path):
  <tmp_path>/
    projects/
      fixture-proj/
        tasks/
          HUMAN-APPROVE-v1.10.0-099/
            status.md   # ## State: WAITING  (or DONE after approve)

Test isolation:
  All filesystem operations are under tmp_path (pytest-managed; redirected
  by the parent conftest.py autouse fixture).  Subprocess invocations set
  PGAI_AGENT_KANBAN_ROOT_PATH to tmp_path and TERM=dumb to suppress ANSI
  escape codes so assertions can be made on plain text output.

Wiring:
  Automatically discovered by pytest because it lives in team/tests/unit/
  and follows the test_*.py naming convention.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Paths to the scripts under test.
#
# The unit test runner (run-unit-tests.sh) sets cwd to team/ before invoking
# pytest.  Absolute paths derived from __file__ keep tests runnable from any
# working directory (e.g. direct pytest invocations from the repo root).
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/tests/unit/ -> team/
_STATUS_RIGHT  = _TEAM_DIR / "scripts" / "dashboard" / "status-right.sh"
_STATUS_BOTTOM = _TEAM_DIR / "scripts" / "dashboard" / "status-bottom.sh"

# Strings asserted on in tests.  NO_COLOR / TERM=dumb mode strips ANSI and
# tmux markup, leaving bare bracketed markers.
_APPROVAL_1   = "APPROVAL(1)"
_APPROVAL_2   = "APPROVAL(2)"
_APPROVAL_ANY = "APPROVAL("   # substring that must be ABSENT when n=0


# ---------------------------------------------------------------------------
# Fixture builder helpers
# ---------------------------------------------------------------------------


def _write_human_approve_task(
    root: pathlib.Path,
    project: str,
    task_id: str,
    state: str,
) -> pathlib.Path:
    """Create a HUMAN-APPROVE task folder with a status.md in the given state.

    Returns the path to status.md so tests can update it in place.
    """
    task_dir = root / "projects" / project / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    status_file = task_dir / "status.md"
    status_file.write_text(
        f"# Status\n\n## State\n{state}\n\n## Needs Human\nyes\n",
        encoding="utf-8",
    )
    return status_file


def _run_status_right(tmp_path: pathlib.Path) -> subprocess.CompletedProcess:
    """Invoke status-right.sh with KANBAN_ROOT=tmp_path, TERM=dumb.

    TERM=dumb disables ANSI escape sequences so assertions work on plain text.
    """
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(tmp_path)
    env["TERM"] = "dumb"
    # Remove variables the conftest autouse fixture may have redirected; the
    # subprocess derives all paths from the explicit KANBAN_ROOT argument.
    env.pop("PGAI_TASKS_DIR", None)
    env.pop("PGAI_QUEUE_DIR", None)
    env.pop("PGAI_RELEASE_STATE_PATH", None)

    return subprocess.run(
        ["bash", str(_STATUS_RIGHT), str(tmp_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _run_status_bottom(tmp_path: pathlib.Path) -> subprocess.CompletedProcess:
    """Invoke status-bottom.sh with KANBAN_ROOT=tmp_path, TERM=dumb."""
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(tmp_path)
    env["TERM"] = "dumb"
    env.pop("PGAI_TASKS_DIR", None)
    env.pop("PGAI_QUEUE_DIR", None)
    env.pop("PGAI_RELEASE_STATE_PATH", None)

    return subprocess.run(
        ["bash", str(_STATUS_BOTTOM), str(tmp_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Tests: status-right.sh
# ---------------------------------------------------------------------------


def test_status_right_one_waiting_task_shows_approval_1(
    tmp_path: pathlib.Path,
) -> None:
    """status-right.sh output contains 'APPROVAL(1)' when one HUMAN-APPROVE task is WAITING."""
    _write_human_approve_task(
        tmp_path, "fixture-proj", "HUMAN-APPROVE-v1.10.0-099", state="WAITING"
    )

    result = _run_status_right(tmp_path)

    assert result.returncode == 0, (
        f"status-right.sh exited with {result.returncode}; stderr: {result.stderr!r}"
    )
    assert _APPROVAL_1 in result.stdout, (
        f"Expected '{_APPROVAL_1}' in status-right.sh output but it was absent.\n"
        f"stdout: {result.stdout!r}"
    )


def test_status_right_one_backlog_task_shows_approval_1(
    tmp_path: pathlib.Path,
) -> None:
    """status-right.sh output contains 'APPROVAL(1)' when one HUMAN-APPROVE task is BACKLOG."""
    _write_human_approve_task(
        tmp_path, "fixture-proj", "HUMAN-APPROVE-v1.10.0-100", state="BACKLOG"
    )

    result = _run_status_right(tmp_path)

    assert result.returncode == 0
    assert _APPROVAL_1 in result.stdout, (
        f"Expected '{_APPROVAL_1}' in output for BACKLOG task; stdout: {result.stdout!r}"
    )


def test_status_right_two_pending_tasks_across_projects_shows_approval_2(
    tmp_path: pathlib.Path,
) -> None:
    """status-right.sh output contains 'APPROVAL(2)' when two tasks across two projects are pending."""
    _write_human_approve_task(
        tmp_path, "proj-alpha", "HUMAN-APPROVE-v1.10.0-001", state="WAITING"
    )
    _write_human_approve_task(
        tmp_path, "proj-beta", "HUMAN-APPROVE-v1.10.0-002", state="BACKLOG"
    )

    result = _run_status_right(tmp_path)

    assert result.returncode == 0
    assert _APPROVAL_2 in result.stdout, (
        f"Expected '{_APPROVAL_2}' in output; stdout: {result.stdout!r}"
    )


def test_status_right_no_pending_tasks_omits_approval_flag(
    tmp_path: pathlib.Path,
) -> None:
    """status-right.sh output contains no 'APPROVAL(' string when no tasks are pending."""
    _write_human_approve_task(
        tmp_path, "fixture-proj", "HUMAN-APPROVE-v1.10.0-099", state="DONE"
    )

    result = _run_status_right(tmp_path)

    assert result.returncode == 0
    assert _APPROVAL_ANY not in result.stdout, (
        f"Unexpected approval indicator in output with no pending tasks; "
        f"stdout: {result.stdout!r}"
    )


def test_status_right_no_human_approve_tasks_omits_approval_flag(
    tmp_path: pathlib.Path,
) -> None:
    """status-right.sh omits 'APPROVAL(' when there are no HUMAN-APPROVE tasks at all."""
    # No HUMAN-APPROVE tasks — empty projects directory
    (tmp_path / "projects" / "fixture-proj" / "tasks").mkdir(parents=True)

    result = _run_status_right(tmp_path)

    assert result.returncode == 0
    assert _APPROVAL_ANY not in result.stdout, (
        f"Unexpected approval indicator in output with no tasks; "
        f"stdout: {result.stdout!r}"
    )


def test_status_right_approve_clears_flag(
    tmp_path: pathlib.Path,
) -> None:
    """Behavioral lifecycle: inject → flag present → approve → flag absent on next render.

    This is the load-bearing acceptance criterion.  A stale ✋ APPROVAL after
    approval would train operators to ignore the flag.

    Steps:
      1. Write a HUMAN-APPROVE task in WAITING state.
      2. Run status-right.sh — APPROVAL(1) must be present.
      3. Update the task status.md to DONE (approve it).
      4. Run status-right.sh again — APPROVAL flag must be absent.
    """
    # Step 1: inject pending approval gate
    status_file = _write_human_approve_task(
        tmp_path, "fixture-proj", "HUMAN-APPROVE-v1.10.0-099", state="WAITING"
    )

    # Step 2: flag present
    result_before = _run_status_right(tmp_path)
    assert result_before.returncode == 0, (
        f"status-right.sh (before approve) exited {result_before.returncode}; "
        f"stderr: {result_before.stderr!r}"
    )
    assert _APPROVAL_1 in result_before.stdout, (
        f"Expected '{_APPROVAL_1}' in output before approval; "
        f"stdout: {result_before.stdout!r}"
    )

    # Step 3: approve — update State to DONE
    status_file.write_text(
        "# Status\n\n## State\nDONE\n\n## Needs Human\nno\n",
        encoding="utf-8",
    )

    # Step 4: flag absent
    result_after = _run_status_right(tmp_path)
    assert result_after.returncode == 0, (
        f"status-right.sh (after approve) exited {result_after.returncode}; "
        f"stderr: {result_after.stderr!r}"
    )
    assert _APPROVAL_ANY not in result_after.stdout, (
        f"Approval flag still present after task approved to DONE; "
        f"stdout: {result_after.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Tests: status-bottom.sh
# ---------------------------------------------------------------------------


def test_status_bottom_one_waiting_task_shows_approval_1(
    tmp_path: pathlib.Path,
) -> None:
    """status-bottom.sh output contains 'APPROVAL(1)' when one HUMAN-APPROVE task is WAITING."""
    _write_human_approve_task(
        tmp_path, "fixture-proj", "HUMAN-APPROVE-v1.10.0-099", state="WAITING"
    )

    result = _run_status_bottom(tmp_path)

    assert result.returncode == 0, (
        f"status-bottom.sh exited with {result.returncode}; stderr: {result.stderr!r}"
    )
    assert _APPROVAL_1 in result.stdout, (
        f"Expected '{_APPROVAL_1}' in status-bottom.sh output but it was absent.\n"
        f"stdout: {result.stdout!r}"
    )


def test_status_bottom_no_pending_tasks_omits_approval_flag(
    tmp_path: pathlib.Path,
) -> None:
    """status-bottom.sh output contains no 'APPROVAL(' when no tasks are pending."""
    _write_human_approve_task(
        tmp_path, "fixture-proj", "HUMAN-APPROVE-v1.10.0-099", state="DONE"
    )

    result = _run_status_bottom(tmp_path)

    assert result.returncode == 0
    assert _APPROVAL_ANY not in result.stdout, (
        f"Unexpected approval indicator in output with no pending tasks; "
        f"stdout: {result.stdout!r}"
    )


def test_status_bottom_approve_clears_flag(
    tmp_path: pathlib.Path,
) -> None:
    """status-bottom.sh: inject → flag present → approve → flag absent on next render."""
    # Inject pending approval gate
    status_file = _write_human_approve_task(
        tmp_path, "fixture-proj", "HUMAN-APPROVE-v1.10.0-099", state="WAITING"
    )

    # Flag must be present before approval
    result_before = _run_status_bottom(tmp_path)
    assert result_before.returncode == 0
    assert _APPROVAL_1 in result_before.stdout, (
        f"Expected '{_APPROVAL_1}' before approval; stdout: {result_before.stdout!r}"
    )

    # Approve: set State to DONE
    status_file.write_text(
        "# Status\n\n## State\nDONE\n\n## Needs Human\nno\n",
        encoding="utf-8",
    )

    # Flag must be absent after approval
    result_after = _run_status_bottom(tmp_path)
    assert result_after.returncode == 0
    assert _APPROVAL_ANY not in result_after.stdout, (
        f"Approval flag still present after DONE in status-bottom.sh; "
        f"stdout: {result_after.stdout!r}"
    )


def test_status_bottom_wontdo_task_omits_approval_flag(
    tmp_path: pathlib.Path,
) -> None:
    """status-bottom.sh: a WONT-DO HUMAN-APPROVE task does not trigger the flag."""
    _write_human_approve_task(
        tmp_path, "fixture-proj", "HUMAN-APPROVE-v1.10.0-099", state="WONT-DO"
    )

    result = _run_status_bottom(tmp_path)

    assert result.returncode == 0
    assert _APPROVAL_ANY not in result.stdout, (
        f"Unexpected approval indicator for WONT-DO task; stdout: {result.stdout!r}"
    )
