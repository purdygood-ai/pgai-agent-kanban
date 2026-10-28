"""
test_task_liveness_record_reap.py
=================================
Behavioral coverage for the two passes that remove dispatch-liveness records
from $KANBAN_ROOT/locks/task-liveness/:

  * reconcile_terminal_liveness_records (wake_reconcile_sweeps.sh), the wake
    reconcile pass: removes the record of a task in this project whose own
    status.md reads DONE or WONT-DO.
  * cleanup.sh Step 2b, the retention sweep: deletes a record whose task
    folder exists in no registered project.

Locked contracts:

  (A) The reconcile pass removes the records of a DONE and a WONT-DO task and
      keeps the record of a WORKING task whose agent is alive.
  (B) It keeps the record of every non-terminal state (BACKLOG, WAITING,
      WORKING, BLOCKED), even when the recorded pid is dead.
  (C) It keeps a DONE task's record while that task's agent is alive.
  (D) It keeps a DONE task's record while the task's worktree directory still
      exists (the worktree sweep needs the dead-pid record to retry teardown).
  (E) It leaves a record whose task folder is not under this project's
      TASKS_ROOT.
  (F) cleanup.sh deletes a record whose task folder exists in no project
      (archived or deleted) and keeps records whose task folder exists, in
      this project or another registered one.
  (G) A --dry-run deletes no record.
  (H) A record whose task exists in no project is kept while its agent is
      alive.
  (I) With no project registered, cleanup.sh deletes no record.
  (J) --help describes Step 2b.

The reconcile cases source the real libraries and call the real function
against a tmp_path KANBAN_ROOT. Every cleanup.sh run uses a throwaway kanban
root under tmp_path with all five live-install names
(PGAI_AGENT_KANBAN_ROOT_PATH, KANBAN_ROOT, PGAI_TASKS_DIR, PGAI_PROJECT_ROOT,
PGAI_AGENT_KANBAN_TEMP_DIR) set or cleared, PGAI_PROJECT_NAME cleared and
HOME pointed at an empty directory (docs/test-authoring-guidelines.md,
"Live-install safety"). Nothing here touches the live install.
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap

from tests.fixtures.log_stub import make_log_stub_fragment
from tests.unit.shell_harness import run_bash

_CLEANUP_SCRIPT = "scripts/cleanup/cleanup.sh"
_LIB = pathlib.Path("scripts/lib")

# A pid this high does not exist: the steady state of a finished dispatch.
_DEAD_PID = "99999999"


def _diagnostic(result) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _write_status(tasks_root: pathlib.Path, task_id: str, state: str) -> None:
    task_dir = tasks_root / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{task_id}\n\n## State\n{state}\n\n## Summary\nfixture.\n",
        encoding="utf-8",
    )


def _write_record(kanban_root: pathlib.Path, task_id: str, pid: str) -> pathlib.Path:
    record_dir = kanban_root / "locks" / "task-liveness"
    record_dir.mkdir(parents=True, exist_ok=True)
    record = record_dir / f"{task_id}.record"
    record.write_text(
        f"holder=claude\npid={pid}\nop=dispatch\ntask={task_id}\npid_start=\nboot_id=\n",
        encoding="utf-8",
    )
    return record


def _record(kanban_root: pathlib.Path, task_id: str) -> pathlib.Path:
    return kanban_root / "locks" / "task-liveness" / f"{task_id}.record"


# ---------------------------------------------------------------------------
# Reconcile pass
# ---------------------------------------------------------------------------


def _sweep_header(tmp_path: pathlib.Path, tasks_root: pathlib.Path) -> str:
    """Source the real libraries the reconcile pass depends on, with a
    tmp_path KANBAN_ROOT and temp dir. PP_project_name and PGAI_WORKTREE_BASE
    are cleared so pgai_worktree_path resolves to <temp>/worktrees/<id>."""
    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        export PGAI_AGENT_KANBAN_TEMP_DIR="{tmp_path / "temp"}"
        mkdir -p "$PGAI_AGENT_KANBAN_TEMP_DIR"
        unset PP_project_name PGAI_WORKTREE_BASE PGAI_AGENT_KANBAN_ROOT_PATH
        KANBAN_ROOT="{tmp_path}"
        TASKS_ROOT="{tasks_root}"
        source {_LIB / "temp.sh"}
        source {_LIB / "worktree.sh"}
        source {_LIB / "lock_provenance.sh"}
        source {_LIB / "md_field.sh"}
        source {_LIB / "wake_task_state.sh"}
        source {_LIB / "wake_reconcile_sweeps.sh"}
        {make_log_stub_fragment(tmp_path / "log.txt")}
    """)


def _run_script(tmp_path: pathlib.Path, script: str) -> subprocess.CompletedProcess:
    script_path = tmp_path / "run_script.sh"
    script_path.write_text(script, encoding="utf-8")
    return subprocess.run(["bash", str(script_path)], capture_output=True, text=True, timeout=60)


def test_reconcile_removes_done_and_wont_do_records_and_keeps_live_working(tmp_path: pathlib.Path) -> None:
    tasks_root = tmp_path / "tasks"
    _write_status(tasks_root, "CODER-20260101-001-finished", "DONE")
    _write_status(tasks_root, "CODER-20260101-002-declined", "WONT-DO")
    _write_status(tasks_root, "CODER-20260101-003-running", "WORKING")
    _write_record(tmp_path, "CODER-20260101-001-finished", _DEAD_PID)
    _write_record(tmp_path, "CODER-20260101-002-declined", _DEAD_PID)

    script = _sweep_header(tmp_path, tasks_root) + textwrap.dedent("""\
        sleep 30 &
        live_pid=$!
        task_liveness_record "CODER-20260101-003-running" claude "$live_pid" dispatch
        reconcile_terminal_liveness_records
        kill "$live_pid" 2>/dev/null || true
        wait "$live_pid" 2>/dev/null || true
    """)
    result = _run_script(tmp_path, script)
    assert result.returncode == 0, _diagnostic(result)

    assert not _record(tmp_path, "CODER-20260101-001-finished").exists(), _diagnostic(result)
    assert not _record(tmp_path, "CODER-20260101-002-declined").exists(), _diagnostic(result)
    assert _record(tmp_path, "CODER-20260101-003-running").exists(), _diagnostic(result)
    assert "removed 2 liveness record(s)" in (tmp_path / "log.txt").read_text(encoding="utf-8")


def test_reconcile_keeps_records_of_every_non_terminal_state(tmp_path: pathlib.Path) -> None:
    tasks_root = tmp_path / "tasks"
    task_ids = {
        "BACKLOG": "CODER-20260101-011-backlog",
        "WAITING": "CODER-20260101-012-waiting",
        "WORKING": "CODER-20260101-013-working",
        "BLOCKED": "CODER-20260101-014-blocked",
    }
    for state, task_id in task_ids.items():
        _write_status(tasks_root, task_id, state)
        # A dead pid isolates the state check: liveness alone would allow it.
        _write_record(tmp_path, task_id, _DEAD_PID)

    result = _run_script(tmp_path, _sweep_header(tmp_path, tasks_root) + "reconcile_terminal_liveness_records\n")
    assert result.returncode == 0, _diagnostic(result)
    for state, task_id in task_ids.items():
        assert _record(tmp_path, task_id).exists(), f"{state} record was removed\n{_diagnostic(result)}"


def test_reconcile_keeps_done_record_while_its_agent_is_alive(tmp_path: pathlib.Path) -> None:
    tasks_root = tmp_path / "tasks"
    task_id = "CODER-20260101-021-done-agent-exiting"
    _write_status(tasks_root, task_id, "DONE")

    script = _sweep_header(tmp_path, tasks_root) + textwrap.dedent(f"""\
        sleep 30 &
        live_pid=$!
        task_liveness_record "{task_id}" claude "$live_pid" dispatch
        reconcile_terminal_liveness_records
        kill "$live_pid" 2>/dev/null || true
        wait "$live_pid" 2>/dev/null || true
    """)
    result = _run_script(tmp_path, script)
    assert result.returncode == 0, _diagnostic(result)
    assert _record(tmp_path, task_id).exists(), _diagnostic(result)


def test_reconcile_keeps_done_record_while_its_worktree_exists(tmp_path: pathlib.Path) -> None:
    tasks_root = tmp_path / "tasks"
    task_id = "CODER-20260101-031-done-teardown-pending"
    _write_status(tasks_root, task_id, "DONE")
    _write_record(tmp_path, task_id, _DEAD_PID)
    (tmp_path / "temp" / "worktrees" / task_id).mkdir(parents=True)

    result = _run_script(tmp_path, _sweep_header(tmp_path, tasks_root) + "reconcile_terminal_liveness_records\n")
    assert result.returncode == 0, _diagnostic(result)
    assert _record(tmp_path, task_id).exists(), _diagnostic(result)


def test_reconcile_leaves_records_of_tasks_outside_this_project(tmp_path: pathlib.Path) -> None:
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    task_id = "CODER-20260101-041-other-project"
    _write_record(tmp_path, task_id, _DEAD_PID)

    result = _run_script(tmp_path, _sweep_header(tmp_path, tasks_root) + "reconcile_terminal_liveness_records\n")
    assert result.returncode == 0, _diagnostic(result)
    assert _record(tmp_path, task_id).exists(), _diagnostic(result)


# ---------------------------------------------------------------------------
# cleanup.sh Step 2b
# ---------------------------------------------------------------------------

_DEMO = "demo"
_OTHER = "other"
_IN_DEMO = "CODER-20260101-051-in-demo"
_IN_OTHER = "CODER-20260101-052-in-other"
_ARCHIVED = "CODER-20260101-053-archived"
_DELETED = "CODER-20260101-054-deleted"


def _cleanup_sandbox(tmp_path: pathlib.Path, *, register: bool = True) -> tuple[pathlib.Path, dict[str, str]]:
    """Two-project kanban root with a record for a task in each project, one
    for an archived task and one for a deleted task. Returns the root and the
    environment that sandboxes cleanup.sh in it."""
    root = tmp_path / "kanban_root"
    (root / "logs" / "agents").mkdir(parents=True)
    cfg = f"[project:{_DEMO}]\npriority = 1\n\n[project:{_OTHER}]\npriority = 2\n" if register else ""
    (root / "projects.cfg").write_text(cfg, encoding="utf-8")
    for project in (_DEMO, _OTHER):
        (root / "projects" / project).mkdir(parents=True)
        (root / "projects" / project / "release-state.md").write_text(
            "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n",
            encoding="utf-8",
        )
    _write_status(root / "projects" / _DEMO / "tasks", _IN_DEMO, "DONE")
    _write_status(root / "projects" / _OTHER / "tasks", _IN_OTHER, "WORKING")
    _write_status(root / "archive" / _DEMO / "tasks", _ARCHIVED, "DONE")
    for task_id in (_IN_DEMO, _IN_OTHER, _ARCHIVED, _DELETED):
        _write_record(root, task_id, _DEAD_PID)
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "temp"),
        "PGAI_TASKS_DIR": "",
        "PGAI_PROJECT_ROOT": "",
        "PGAI_PROJECT_NAME": "",
        "HOME": str(home),
    }
    return root, env


def test_cleanup_deletes_record_of_task_in_no_project_and_keeps_existing_ones(tmp_path: pathlib.Path) -> None:
    root, env = _cleanup_sandbox(tmp_path)

    result = run_bash(tmp_path, f"bash {_CLEANUP_SCRIPT} --project {_DEMO}", extra_env=env)

    assert result.returncode == 0, _diagnostic(result)
    assert not _record(root, _ARCHIVED).exists(), _diagnostic(result)
    assert not _record(root, _DELETED).exists(), _diagnostic(result)
    assert _record(root, _IN_DEMO).exists(), _diagnostic(result)
    # A task in another registered project is found too: the directory is
    # kanban-wide, so the search is as well.
    assert _record(root, _IN_OTHER).exists(), _diagnostic(result)
    assert "task-liveness records deleted: 2" in result.stdout, _diagnostic(result)
    assert "liveness_records_deleted: 2" in result.stdout, _diagnostic(result)


def test_cleanup_dry_run_deletes_no_record(tmp_path: pathlib.Path) -> None:
    root, env = _cleanup_sandbox(tmp_path)

    result = run_bash(tmp_path, f"bash {_CLEANUP_SCRIPT} --project {_DEMO} --dry-run", extra_env=env)

    assert result.returncode == 0, _diagnostic(result)
    for task_id in (_IN_DEMO, _IN_OTHER, _ARCHIVED, _DELETED):
        assert _record(root, task_id).exists(), f"{task_id}\n{_diagnostic(result)}"
    assert "[DRY-RUN] delete task-liveness record" in result.stdout, _diagnostic(result)


def test_cleanup_keeps_record_of_vanished_task_whose_agent_is_alive(tmp_path: pathlib.Path) -> None:
    root, env = _cleanup_sandbox(tmp_path)
    live = subprocess.Popen(["sleep", "30"])
    try:
        _write_record(root, _DELETED, str(live.pid))
        result = run_bash(tmp_path, f"bash {_CLEANUP_SCRIPT} --project {_DEMO}", extra_env=env)
    finally:
        live.kill()
        live.wait()

    assert result.returncode == 0, _diagnostic(result)
    assert _record(root, _DELETED).exists(), _diagnostic(result)
    assert not _record(root, _ARCHIVED).exists(), _diagnostic(result)


def test_cleanup_deletes_no_record_when_no_project_is_registered(tmp_path: pathlib.Path) -> None:
    root, env = _cleanup_sandbox(tmp_path, register=False)

    result = run_bash(tmp_path, f"bash {_CLEANUP_SCRIPT} --project {_DEMO}", extra_env=env)

    assert result.returncode == 0, _diagnostic(result)
    for task_id in (_IN_DEMO, _IN_OTHER, _ARCHIVED, _DELETED):
        assert _record(root, task_id).exists(), f"{task_id}\n{_diagnostic(result)}"
    assert "Step 2b skipped" in result.stdout, _diagnostic(result)


def test_cleanup_help_describes_liveness_record_step(tmp_path: pathlib.Path) -> None:
    result = run_bash(tmp_path, f"bash {_CLEANUP_SCRIPT} --help")

    assert result.returncode == 0, _diagnostic(result)
    assert "Step 2b" in result.stdout
    assert "task-liveness records" in result.stdout
