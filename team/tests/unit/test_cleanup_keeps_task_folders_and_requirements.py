"""
test_cleanup_keeps_task_folders_and_requirements.py
===================================================
Behavioral unit tests for what team/scripts/cleanup/cleanup.sh leaves alone:
task folders and requirements. Finished work leaves a live project only
through the archive run (scripts/archive.sh); cleanup never deletes a task
folder and keeps no requirements archive of its own.

Locked contracts:

  1. A real (non-dry) run leaves DONE and WONT-DO task folders far older
     than the task retention in place, byte for byte; Step 2 only counts
     them and names the archive dry-run command.
  2. A real run leaves a shipped requirement (Target Version <= Last
     Released) in requirements/, and creates no
     tasks/archive/requirements/ directory (nor its priority/ child).
  3. The dry-run output narrates no task-folder deletion and no
     requirements archival, and reports the kept count.
  4. --help says task folders are never deleted and lists no Step 3.
  5. Nothing under $KANBAN_ROOT/archive/<project>/ is touched by a run.

Every invocation runs against a throwaway kanban root built under pytest's
tmp_path with all five live-install names (PGAI_AGENT_KANBAN_ROOT_PATH,
KANBAN_ROOT, PGAI_TASKS_DIR, PGAI_PROJECT_ROOT, PGAI_AGENT_KANBAN_TEMP_DIR)
set or cleared, PGAI_PROJECT_NAME cleared, and HOME pointed at an empty
directory so no user-level config file can redirect a path. Nothing here
points cleanup.sh at the live install.
"""

from __future__ import annotations

import os
import pathlib
import time

from tests.unit.shell_harness import run_bash

_CLEANUP_SCRIPT = "scripts/cleanup/cleanup.sh"
_PROJECT = "demo"
_OLD = time.time() - (60 * 86400)


def _diagnostic(result) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _tree(root: pathlib.Path) -> dict[str, bytes]:
    """Every file under *root*, relative, mapped to its bytes."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _backdate(path: pathlib.Path) -> None:
    os.utime(path, (_OLD, _OLD))


def _sandbox(tmp_path: pathlib.Path) -> tuple[pathlib.Path, dict[str, str]]:
    """A one-project kanban root holding old terminal task folders (both
    task-ID shapes), an unfinished one, a shipped requirement, a priority
    requirement and an archive tree. Returns the root and the environment
    that sandboxes cleanup.sh in it."""
    root = tmp_path / "kanban_root"
    project = root / "projects" / _PROJECT
    tasks = project / "tasks"
    (root / "logs" / "agents").mkdir(parents=True)
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
    (project / "release-state.md").parent.mkdir(parents=True)
    (project / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n\n## Last Released\nv2.0.0\n",
        encoding="utf-8",
    )
    for task_id, state in (
        ("CODER-20260101-001-finished-work", "DONE"),
        ("CLAUDE-20260101-002-declined-work", "WONT-DO"),
        ("CODER-20260101-003-still-working", "WORKING"),
    ):
        task_dir = tasks / task_id
        (task_dir / "artifacts").mkdir(parents=True)
        (task_dir / "artifacts" / "report.md").write_text(f"report of {task_id}\n", encoding="utf-8")
        status = task_dir / "status.md"
        status.write_text(f"# Status\n\n## State\n{state}\n", encoding="utf-8")
        _backdate(status)
        _backdate(task_dir)
    (tasks / "queues").mkdir()
    (tasks / "queues" / "coder_backlog.md").write_text("- [x] CODER-20260101-001-finished-work\n", encoding="utf-8")
    requirements = project / "requirements"
    (requirements / "priority").mkdir(parents=True)
    (requirements / "v1.0.0-shipped-feature.md").write_text(
        "# v1.0.0-shipped-feature\n\n## Status\ndone\n\n## Target Version\nv1.0.0\n", encoding="utf-8"
    )
    (requirements / "priority" / "v1.0.1-priority-bundle.md").write_text(
        "# v1.0.1-priority-bundle\n\n## Status\ndone\n\n## Target Version\nv1.0.1\n", encoding="utf-8"
    )
    archived = root / "archive" / _PROJECT / "tasks" / "CODER-20250101-001-archived-long-ago"
    archived.mkdir(parents=True)
    (archived / "status.md").write_text("# Status\n\n## State\nDONE\n", encoding="utf-8")
    _backdate(archived / "status.md")
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


# ---------------------------------------------------------------------------
# 1, 2, 5. A real run deletes no task folder, archives no requirement, and
#          leaves the archive tree alone
# ---------------------------------------------------------------------------


def test_real_run_keeps_old_terminal_task_folders_and_shipped_requirements(tmp_path: pathlib.Path) -> None:
    root, env = _sandbox(tmp_path)
    project = root / "projects" / _PROJECT
    tasks_before = _tree(project / "tasks")
    requirements_before = _tree(project / "requirements")
    archive_before = _tree(root / "archive")

    result = run_bash(tmp_path, f"bash {_CLEANUP_SCRIPT} --project {_PROJECT}", extra_env=env)

    assert result.returncode == 0, _diagnostic(result)
    assert _tree(project / "tasks") == tasks_before, _diagnostic(result)
    assert _tree(project / "requirements") == requirements_before, _diagnostic(result)
    assert _tree(root / "archive") == archive_before, _diagnostic(result)
    assert not (project / "tasks" / "archive" / "requirements").exists(), _diagnostic(result)

    tasks_dir = project / "tasks"
    assert f"Step 2: report terminal task folders in {tasks_dir} (none are deleted) ---" in result.stdout
    # The DONE and the WONT-DO folder are counted; the WORKING one is not.
    assert "terminal task folders older than 7d: 2; left in place" in result.stdout, _diagnostic(result)
    assert f"scripts/archive.sh --project {_PROJECT} --dry-run" in result.stdout, _diagnostic(result)
    assert "task_folders_kept:     2" in result.stdout, _diagnostic(result)
    assert "Step 3" not in result.stdout, _diagnostic(result)


# ---------------------------------------------------------------------------
# 3. The dry run narrates no deletion and no requirements archival
# ---------------------------------------------------------------------------


def test_dry_run_narrates_no_task_folder_deletion_or_requirements_archival(tmp_path: pathlib.Path) -> None:
    root, env = _sandbox(tmp_path)
    before = _tree(root / "projects")

    result = run_bash(tmp_path, f"bash {_CLEANUP_SCRIPT} --project {_PROJECT} --dry-run", extra_env=env)

    assert result.returncode == 0, _diagnostic(result)
    assert _tree(root / "projects") == before, _diagnostic(result)
    for absent in ("delete task folder", "archive standard requirement", "archive priority requirement",
                   "requirements archived", "task_folders_deleted"):
        assert absent not in result.stdout, f"{absent!r} narrated:\n{_diagnostic(result)}"
    created = [line for line in result.stdout.splitlines() if "would create directory" in line]
    assert not any("requirements" in line for line in created), _diagnostic(result)
    assert "terminal task folders older than 7d: 2; left in place" in result.stdout, _diagnostic(result)


# ---------------------------------------------------------------------------
# 4. --help
# ---------------------------------------------------------------------------


def test_help_says_task_folders_are_never_deleted_and_lists_no_step_three(tmp_path: pathlib.Path) -> None:
    _root, env = _sandbox(tmp_path)

    result = run_bash(tmp_path, f"bash {_CLEANUP_SCRIPT} --help", extra_env=env)

    assert result.returncode == 0, _diagnostic(result)
    steps = [line.split()[1] for line in result.stdout.splitlines() if line.strip().startswith("Step ")]
    assert steps == ["0", "1", "2", "4", "5", "6"], result.stdout
    assert "task folders are never deleted" in result.stdout, result.stdout
    assert "scripts/archive.sh" in result.stdout, result.stdout
