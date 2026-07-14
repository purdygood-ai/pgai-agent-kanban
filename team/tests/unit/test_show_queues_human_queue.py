"""
test_show_queues_human_queue.py
===============================
Behavioral tests for the human-queue rendering in show-queues.sh --details.

Covers the load-bearing acceptance criterion from an earlier defect / CODER-20260708-023:

  A HUMAN-APPROVE gate task whose human_backlog.md queue entry carries a
  valid marker ('[W]' or ' ') must render in 'show-queues --details' with
  ZERO '⚠ no queue entry' warnings.

Tests:

  1. green path — HUMAN-APPROVE task + valid queue marker in human_backlog.md
     → output contains no '⚠ no queue entry' text.

  2. negative guard — same task but queue file is absent
     → output DOES contain '⚠ no queue entry' for the HUMAN-APPROVE task.
     (Proves the test actually guards the property; not merely asserting on
     an output that never emits the warning regardless of input.)

Fixture structure built by each test (under pytest tmp_path):
  <tmp_path>/
    projects.cfg                      # INI registry registering 'fixture-proj'
    projects/
      fixture-proj/
        tasks/
          HUMAN-APPROVE-v1.10.0-099/
            README.md                 # ## Role: HUMAN
            status.md                 # ## State: WAITING
          queues/
            human_backlog.md          # - [W] HUMAN-APPROVE-v1.10.0-099

Test isolation:
  All filesystem operations are under tmp_path (pytest-managed; redirected
  to PGAI_AGENT_KANBAN_TEMP_DIR/tests by the parent conftest.py autouse
  fixture).  The subprocess invokes show-queues.sh with PGAI_AGENT_KANBAN_ROOT_PATH
  pointing at tmp_path and TERM=dumb to suppress ANSI codes.

Wiring:
  This test is automatically included in the unit suite (run-unit-tests.sh)
  because it lives in team/tests/unit/ and follows the test_*.py naming
  convention that pytest discovers.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Path to show-queues.sh.
#
# The unit test runner (run-unit-tests.sh) sets cwd to team/ before invoking
# pytest, so the script is reachable at scripts/dashboard/show-queues.sh from
# that cwd.  Using an absolute path derived from __file__ keeps the test
# working from any cwd (e.g. direct `pytest` invocations from the repo root).
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/tests/unit/ -> team/
_SHOW_QUEUES = _TEAM_DIR / "scripts" / "dashboard" / "show-queues.sh"

# Fixture: stable HUMAN-APPROVE task ID for the test
_TASK_ID = "HUMAN-APPROVE-v1.10.0-099"

# The warning string that MUST be absent (green-path test) / present (guard test)
_NO_QUEUE_ENTRY_WARNING = "⚠ no queue entry"

# Project name used in fixture
_PROJ = "fixture-proj"


# ---------------------------------------------------------------------------
# Fixture builder helpers
# ---------------------------------------------------------------------------


def _build_fixture(root: pathlib.Path, *, include_queue_entry: bool) -> None:
    """Populate *root* with the minimal kanban structure needed for the tests.

    Args:
        root:                The tmp_path directory to populate.
        include_queue_entry: When True, write human_backlog.md with a valid
                             '[W]' marker for _TASK_ID.  When False, omit the
                             queue file entirely to trigger the warning path.
    """
    # --- projects.cfg (INI format) ---
    (root / "projects.cfg").write_text(
        f"[project:{_PROJ}]\npriority=1\n",
        encoding="utf-8",
    )

    # --- Task folder: HUMAN-APPROVE task ---
    task_dir = root / "projects" / _PROJ / "tasks" / _TASK_ID
    task_dir.mkdir(parents=True, exist_ok=True)

    # README.md — role must be HUMAN so show-queues routes to the human agent
    (task_dir / "README.md").write_text(
        f"# Task: human gate\n\n## Role\nHUMAN\n",
        encoding="utf-8",
    )

    # status.md — WAITING state (the normal state for a newly injected gate)
    (task_dir / "status.md").write_text(
        "# Status\n\n## State\nWAITING\n",
        encoding="utf-8",
    )

    # --- Queue directory ---
    queues_dir = root / "projects" / _PROJ / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)

    if include_queue_entry:
        # Valid [W] marker — task has prerequisites so WAITING is the correct
        # initial marker per queue_marker_for_task semantics.
        (queues_dir / "human_backlog.md").write_text(
            f"# HUMAN Backlog\n- [W] {_TASK_ID}\n",
            encoding="utf-8",
        )


def _run_show_queues(tmp_path: pathlib.Path) -> subprocess.CompletedProcess:
    """Invoke show-queues.sh --details --project fixture-proj against tmp_path.

    Returns the completed process so the caller can inspect stdout/stderr and
    the return code.

    Environment:
      PGAI_AGENT_KANBAN_ROOT_PATH -> tmp_path (redirected away from live install)
      TERM=dumb                   -> disables ANSI color codes for easier assertion
    """
    import os

    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(tmp_path)
    env["TERM"] = "dumb"
    # Clear variables that the autouse _block_live_kanban_writes fixture may
    # have redirected; the subprocess should derive all paths from our root.
    env.pop("PGAI_TASKS_DIR", None)
    env.pop("PGAI_QUEUE_DIR", None)
    env.pop("PGAI_RELEASE_STATE_PATH", None)

    return subprocess.run(
        ["bash", str(_SHOW_QUEUES), "--details", "--project", _PROJ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_human_approve_task_with_valid_queue_marker_renders_without_warning(
    tmp_path: pathlib.Path,
) -> None:
    """show-queues --details emits no '⚠ no queue entry' when human_backlog.md has a valid marker.

    Green-path acceptance criterion (an earlier defect / CODER-20260708-023):
      A HUMAN-APPROVE task whose human_backlog.md carries a '[W]' marker
      renders in the details view with zero '⚠ no queue entry' occurrences.
    """
    _build_fixture(tmp_path, include_queue_entry=True)

    result = _run_show_queues(tmp_path)

    # Script must exit cleanly
    assert result.returncode == 0, (
        f"show-queues.sh exited with {result.returncode}; "
        f"stderr: {result.stderr!r}"
    )

    # Human section must be present — confirms the task was rendered at all
    assert _TASK_ID in result.stdout, (
        f"Expected {_TASK_ID!r} in output but it was absent.\n"
        f"stdout:\n{result.stdout}"
    )

    # The load-bearing assertion: zero '⚠ no queue entry' occurrences
    assert _NO_QUEUE_ENTRY_WARNING not in result.stdout, (
        f"'⚠ no queue entry' warning present in show-queues output for "
        f"{_TASK_ID!r}; human_backlog.md has a valid marker but the "
        f"dashboard still warns.  Output:\n{result.stdout}"
    )


def test_human_approve_task_without_queue_entry_renders_warning(
    tmp_path: pathlib.Path,
) -> None:
    """show-queues --details emits '⚠ no queue entry' when human_backlog.md is absent.

    Negative guard — proves the green-path test is not vacuously true:
    removing the queue file causes the warning to appear, confirming that
    the absence assertion above is a real guard and not a no-op.
    """
    _build_fixture(tmp_path, include_queue_entry=False)

    result = _run_show_queues(tmp_path)

    # Script must exit cleanly regardless of queue state
    assert result.returncode == 0, (
        f"show-queues.sh exited with {result.returncode}; "
        f"stderr: {result.stderr!r}"
    )

    # Human section must be present
    assert _TASK_ID in result.stdout, (
        f"Expected {_TASK_ID!r} in output but it was absent.\n"
        f"stdout:\n{result.stdout}"
    )

    # The guard assertion: warning IS present when queue entry is missing
    assert _NO_QUEUE_ENTRY_WARNING in result.stdout, (
        f"Expected '⚠ no queue entry' warning in output for {_TASK_ID!r} "
        f"when human_backlog.md is absent, but the warning was not found.\n"
        f"stdout:\n{result.stdout}"
    )
