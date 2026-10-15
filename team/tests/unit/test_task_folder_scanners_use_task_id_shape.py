"""
test_task_folder_scanners_use_task_id_shape.py
==============================================
Task-folder scanners select folders by the shared task-ID shape
(team/scripts/lib/task_ids.sh), so current IDs (``PM-YYYYMMDD-NNN-slug``)
and the legacy ``CLAUDE-`` form are both found:

  1. kanban_task_id_role prints a canonical ID's role with or without the
     legacy prefix and refuses anything else; kanban_task_ids_in_files finds
     IDs of either form in free text.
  2. check-readme-bundled (dry run) detects a PM-YYYYMMDD-NNN-slug task whose
     ## Inputs names a README.md, and a legacy CLAUDE-PM-... one; a CODER
     task with the same input is not a PM task and is ignored.
  3. purge_tasks (dry run) lists an old DONE CODER-YYYYMMDD-NNN-slug folder
     and an old DONE legacy CLAUDE-CODER-... folder, never a WORKING one,
     never a non-task directory such as queues/, and never a DONE task the
     Active RC's bundle names (in either form).

Every fixture lives under tmp_path; no live kanban root is read or written.
"""

from __future__ import annotations

import os
import pathlib
import time

from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_TASK_IDS = _TEAM_DIR / "scripts" / "lib" / "task_ids.sh"
_PURGE = _TEAM_DIR / "scripts" / "lib" / "purge-helpers.sh"
_README_CHECK = _TEAM_DIR / "scripts" / "lib" / "overwatch-checks" / "check-readme-bundled.sh"
_OLD = time.time() - 100 * 86400


def _task(tasks: pathlib.Path, task_id: str, state: str, inputs: str = "", *, old: bool = False) -> pathlib.Path:
    folder = tasks / task_id
    folder.mkdir(parents=True)
    (folder / "README.md").write_text(f"# Task: {task_id}\n\n## Inputs\n{inputs}\n\n## Goal\nx\n", encoding="utf-8")
    (folder / "status.md").write_text(f"# Status\n\n## State\n{state}\n", encoding="utf-8")
    if old:
        os.utime(folder, (_OLD, _OLD))
    return folder


def test_role_and_text_scan_accept_both_id_forms(tmp_path: pathlib.Path) -> None:
    text = tmp_path / "bundle.md"
    text.write_text(
        "- CODER-20261012-001-fix-a and `CLAUDE-PM-20260101-002-plan`.\n- BUG-0001-x v1.2.3-bugfix-bundle-20261012.md\n",
        encoding="utf-8",
    )
    script = f"""set -euo pipefail
source '{_TASK_IDS}'
for id in PM-20261012-001-x CLAUDE-PM-20261012-001-x CLAUDE-20261012-001-x bad ../x PM-2026101-001-x; do
  printf '%s=' "$id"; kanban_task_id_role "$id" || echo REFUSED
done
kanban_task_ids_in_files '{text}' '{tmp_path / "missing.md"}'
"""
    result = run_bash(tmp_path, script)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "PM-20261012-001-x=PM",
        "CLAUDE-PM-20261012-001-x=PM",
        "CLAUDE-20261012-001-x=CLAUDE",
        "bad=REFUSED",
        "../x=REFUSED",
        "PM-2026101-001-x=REFUSED",
        "CODER-20261012-001-fix-a",
        "CLAUDE-PM-20260101-002-plan",
    ]


def test_readme_bundled_check_detects_current_and_legacy_pm_tasks(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "kanban"
    project = root / "projects" / "p"
    tasks = project / "tasks"
    (project / "overwatch").mkdir(parents=True)
    (project / "bugs").mkdir()
    bad = "- /some/where/requirements/README.md"
    _task(tasks, "PM-20261012-001-decompose", "BACKLOG", bad)
    _task(tasks, "CLAUDE-PM-20260101-001-legacy", "WORKING", bad)
    _task(tasks, "CODER-20261012-002-not-pm", "BACKLOG", bad)
    _task(tasks, "PM-20261012-003-finished", "DONE", bad)
    (tasks / "queues").mkdir()

    result = run_bash(
        tmp_path,
        f"set -uo pipefail\nbash '{_README_CHECK}' --dry-run\n",
        extra_env={"KANBAN_ROOT": str(root), "OVERWATCH_PROJECT": "p"},
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "check-readme-bundled: PM-20261012-001-decompose: bad input detected" in result.stderr, result.stderr
    assert "check-readme-bundled: CLAUDE-PM-20260101-001-legacy: bad input detected" in result.stderr
    assert "CODER-20261012-002-not-pm" not in result.stderr
    assert "PM-20261012-003-finished" not in result.stderr


def test_task_purge_dry_run_selects_finished_task_folders_of_both_forms(tmp_path: pathlib.Path) -> None:
    project = tmp_path / "projects" / "p"
    tasks = project / "tasks"
    (project / "requirements").mkdir(parents=True)
    (project / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nv1.2.0\n\n## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n",
        encoding="utf-8",
    )
    (project / "requirements" / "v1.2.0-bugfix-bundle-20261012.md").write_text(
        "## Bundled Items\n\n- CODER-20261012-009-in-rc and CLAUDE-CODER-20260101-009-legacy-in-rc\n",
        encoding="utf-8",
    )
    current = _task(tasks, "CODER-20261012-001-done", "DONE", old=True)
    legacy = _task(tasks, "CLAUDE-CODER-20260101-001-done", "WONT-DO", old=True)
    working = _task(tasks, "CODER-20261012-002-working", "WORKING", old=True)
    in_rc = _task(tasks, "CODER-20261012-009-in-rc", "DONE", old=True)
    legacy_in_rc = _task(tasks, "CLAUDE-CODER-20260101-009-legacy-in-rc", "DONE", old=True)
    queues = tasks / "queues"
    queues.mkdir()
    os.utime(queues, (_OLD, _OLD))

    result = run_bash(
        tmp_path,
        f"set -euo pipefail\nsource '{_PURGE}'\npurge_tasks '{project}' 30 false false\n",
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert f"[WOULD PURGE] {current} (state=DONE)" in lines, result.stdout
    assert f"[WOULD PURGE] {legacy} (state=WONT-DO)" in lines, result.stdout
    assert f"[SKIP] {working} (state=WORKING; non-terminal)" in lines, result.stdout
    assert f"[SKIP active-RC] {in_rc} (referenced by active RC v1.2.0)" in lines, result.stdout
    assert f"[SKIP active-RC] {legacy_in_rc} (referenced by active RC v1.2.0)" in lines, result.stdout
    assert not any(str(queues) in line for line in lines), result.stdout
    assert lines[-1].split()[0] == "2", result.stdout
    assert working.is_dir() and current.is_dir(), "a dry run deletes nothing"
