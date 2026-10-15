"""
test_overwatch_backup_by_source_path.py
=======================================
overwatch_backup_file (team/scripts/lib/overwatch_protocol.sh) keys every
backup on its source's own path, and overwatch-revert.sh restores each
backup to the file it was taken from.

Coverage:
  - Two tasks' status.md backed up in one firing land in two distinct
    backup files, each holding its own task's content; backing the same file
    up twice in a firing keeps the first (pre-firing) copy.
  - A source outside the project root is backed up under the absolute-path
    form and still never collides.
  - Reverting the firing restores each task's own status.md, and says
    which backup went where.
  - A firing in which two actions touched the same file reverts it to its
    pre-firing content (rows are undone newest first).

Every path is a throwaway kanban root under pytest's tmp_path.
"""

from __future__ import annotations

import pathlib

from tests.unit.shell_harness import run_bash

_PROJECT = "backup-proj"
_FIRING = "2026-03-03T03:03:03Z"


def _sandbox(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "kanban"
    project_root = root / "projects" / _PROJECT
    (project_root / "overwatch" / "backups").mkdir(parents=True)
    (project_root / "overwatch" / "actions.log").write_text("", encoding="utf-8")
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
    (project_root / "project.cfg").write_text(f"[project]\nproject_name = {_PROJECT}\n", encoding="utf-8")
    (root / "kanban.cfg").write_text("[wake]\n", encoding="utf-8")
    for task in ("TASK-A", "TASK-B"):
        (project_root / "tasks" / task).mkdir(parents=True)
        (project_root / "tasks" / task / "status.md").write_text(f"# Status\n\n## State\nBLOCKED\n\n{task}\n",
                                                                encoding="utf-8")
    return root


def _env(root: pathlib.Path) -> dict:
    (root / "tmp").mkdir(exist_ok=True)
    return {
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        "OVERWATCH_PROJECT": _PROJECT,
        "OVERWATCH_FIRING_TIMESTAMP": _FIRING,
    }


def _backup_and_log(tmp_path: pathlib.Path, root: pathlib.Path, task: str) -> str:
    status = root / "projects" / _PROJECT / "tasks" / task / "status.md"
    script = (
        "set -euo pipefail\nsource scripts/lib/overwatch_protocol.sh\n"
        f"bp=\"$(overwatch_backup_file '{status}')\"\n"
        f"overwatch_log_action fixture-check {task} requeue \"$bp\" 'fixture fix'\n"
        f"printf 'new state\\n' > '{status}'\n"
        "echo \"BACKUP=$bp\"\n"
    )
    result = run_bash(tmp_path, script, extra_env=_env(root))
    assert result.returncode == 0, result.stderr
    return next(line.split("=", 1)[1] for line in result.stdout.splitlines() if line.startswith("BACKUP="))


def test_two_tasks_status_files_get_distinct_backups_in_one_firing(tmp_path):
    root = _sandbox(tmp_path)

    backup_a = _backup_and_log(tmp_path, root, "TASK-A")
    backup_b = _backup_and_log(tmp_path, root, "TASK-B")

    assert backup_a != backup_b
    assert pathlib.Path(backup_a).read_text(encoding="utf-8").endswith("TASK-A\n")
    assert pathlib.Path(backup_b).read_text(encoding="utf-8").endswith("TASK-B\n")
    assert backup_a.endswith(f"/backups/{_FIRING}/project-root/tasks/TASK-A/status.md")


def test_backing_up_the_same_file_twice_keeps_the_pre_firing_copy(tmp_path):
    root = _sandbox(tmp_path)
    first = _backup_and_log(tmp_path, root, "TASK-A")  # rewrites status to "new state"
    second = _backup_and_log(tmp_path, root, "TASK-A")

    assert first == second
    assert pathlib.Path(first).read_text(encoding="utf-8").endswith("TASK-A\n")


def test_a_source_outside_the_project_root_uses_the_absolute_form(tmp_path):
    root = _sandbox(tmp_path)
    outside = tmp_path / "elsewhere" / "status.md"
    outside.parent.mkdir()
    outside.write_text("outside\n", encoding="utf-8")
    script = f"source scripts/lib/overwatch_protocol.sh && overwatch_backup_file '{outside}'"

    result = run_bash(tmp_path, script, extra_env=_env(root))

    assert result.returncode == 0, result.stderr
    backup = result.stdout.strip()
    assert backup.endswith(f"/backups/{_FIRING}/absolute{outside}"), backup
    assert pathlib.Path(backup).read_text(encoding="utf-8") == "outside\n"


def test_reverting_the_firing_restores_each_tasks_own_status(tmp_path):
    root = _sandbox(tmp_path)
    tasks = root / "projects" / _PROJECT / "tasks"
    originals = {t: (tasks / t / "status.md").read_text(encoding="utf-8") for t in ("TASK-A", "TASK-B")}
    _backup_and_log(tmp_path, root, "TASK-A")
    _backup_and_log(tmp_path, root, "TASK-B")

    result = run_bash(tmp_path, f"bash scripts/overwatch-revert.sh --project {_PROJECT} {_FIRING}",
                      extra_env=_env(root))

    assert result.returncode == 0, result.stdout + result.stderr
    for task, text in originals.items():
        assert (tasks / task / "status.md").read_text(encoding="utf-8") == text, result.stdout
    assert f"-> {tasks / 'TASK-A' / 'status.md'}" in result.stdout
    assert f"-> {tasks / 'TASK-B' / 'status.md'}" in result.stdout
    assert "Files restored: 2" in result.stdout


def test_a_file_two_actions_touched_reverts_to_its_pre_firing_content(tmp_path):
    root = _sandbox(tmp_path)
    status = root / "projects" / _PROJECT / "tasks" / "TASK-A" / "status.md"
    original = status.read_text(encoding="utf-8")
    _backup_and_log(tmp_path, root, "TASK-A")
    _backup_and_log(tmp_path, root, "TASK-A")

    result = run_bash(tmp_path, f"bash scripts/overwatch-revert.sh --project {_PROJECT} {_FIRING}",
                      extra_env=_env(root))

    assert result.returncode == 0, result.stdout + result.stderr
    assert status.read_text(encoding="utf-8") == original
