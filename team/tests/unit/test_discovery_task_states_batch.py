"""
test_discovery_task_states_batch.py
===================================
The batched queue-state read the discovery chain-busy test runs on:
``pgai_agent_kanban.discovery.items task-states`` (one process answers a
whole queue) and its bash wrapper ``_disc_backlog_task_states``
(team/scripts/lib/discovery_guards.sh).

Both must classify every entry exactly as the single-task reader
(``task-state`` / ``_disc_task_terminal``) does -- DONE, WONT-DO, any other
state verbatim, NO-FOLDER for a missing status.md, UNPARSEABLE for an
unreadable ## State -- because the chain-idle and pending-PM-ticket guards
decide from those words.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

from pgai_agent_kanban.discovery.items import task_states
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]


def _task(tasks_dir: pathlib.Path, task_id: str, status_body: str) -> None:
    folder = tasks_dir / task_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "status.md").write_text(status_body, encoding="utf-8")


def _tasks(tmp_path: pathlib.Path) -> pathlib.Path:
    tasks_dir = tmp_path / "tasks"
    _task(tasks_dir, "CODER-1-done", "# Status\n\n## State\nDONE\n")
    _task(tasks_dir, "CODER-2-working", "# Status\n\n## State\nworking\n")
    _task(tasks_dir, "CODER-3-wontdo", "# Status\n\n## State\nWONT-DO\n")
    _task(tasks_dir, "CODER-4-broken", "# Status\n\n## Summary\nno state section\n")
    return tasks_dir


def test_states_come_back_in_input_order_with_missing_and_broken_named(tmp_path):
    tasks_dir = _tasks(tmp_path)
    ids = ["CODER-2-working", "", "CODER-9-missing", "CODER-1-done", "CODER-4-broken", "CODER-3-wontdo"]

    assert task_states(str(tasks_dir), ids) == [
        ("CODER-2-working", "WORKING"),
        ("CODER-9-missing", "NO-FOLDER"),
        ("CODER-1-done", "DONE"),
        ("CODER-4-broken", "UNPARSEABLE"),
        ("CODER-3-wontdo", "WONT-DO"),
    ]


def test_cli_reads_ids_on_stdin_and_prints_tab_separated_lines(tmp_path):
    tasks_dir = _tasks(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.discovery.items", "task-states", str(tasks_dir)],
        input="CODER-1-done\nCODER-2-working\n\nCODER-9-missing\n",
        capture_output=True,
        text=True,
        cwd=str(_TEAM_DIR),
        env=build_scrubbed_env(home_dir=tmp_path, extra_env={"PYTHONPATH": str(_TEAM_DIR)}),
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "CODER-1-done\tDONE",
        "CODER-2-working\tWORKING",
        "CODER-9-missing\tNO-FOLDER",
    ]


def test_bash_wrapper_reads_every_queue_entry_and_honours_the_id_filter(two_project_root, tmp_path):
    root = two_project_root
    tasks_dir = root / "projects" / "project_a" / "tasks"
    _task(tasks_dir, "PM-1-decompose-v0-1-0-x", "# Status\n\n## State\nDONE\n")
    _task(tasks_dir, "PM-2-decompose-v0-1-1-y", "# Status\n\n## State\nBACKLOG\n")
    _task(tasks_dir, "PM-3-other", "# Status\n\n## State\nWORKING\n")
    backlog = tasks_dir / "queues" / "pm_backlog.md"
    backlog.write_text(
        "# PM Backlog\n\n"
        "- [x] PM-1-decompose-v0-1-0-x — done\n"
        "-   [ ]   PM-2-decompose-v0-1-1-y — queued\n"
        "- [A] PM-3-other — another kind of ticket\n"
        "not a list item PM-4\n",
        encoding="utf-8",
    )
    env = build_scrubbed_env(
        home_dir=tmp_path,
        extra_env={
            "KANBAN_ROOT": str(root),
            "TEAM_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "scratch"),
        },
    )
    script = (
        "set -euo pipefail\n"
        "source scripts/lib/ini_parser.sh\n"
        "source scripts/lib/temp.sh\n"
        "source scripts/lib/project_paths.sh\n"
        "source scripts/lib/discovery.sh\n"
        f'echo "ALL"; _disc_backlog_task_states project_a "{backlog}"\n'
        f'echo "FILTERED"; _disc_backlog_task_states project_a "{backlog}" "decompose-v[0-9]"\n'
    )

    result = run_bash(tmp_path, script, base_env=env, timeout=60)

    assert result.returncode == 0, result.stderr
    out = result.stdout.split("FILTERED\n")
    assert out[0].splitlines() == [
        "ALL",
        "PM-1-decompose-v0-1-0-x\tDONE",
        "PM-2-decompose-v0-1-1-y\tBACKLOG",
        "PM-3-other\tWORKING",
    ]
    assert out[1].splitlines() == [
        "PM-1-decompose-v0-1-0-x\tDONE",
        "PM-2-decompose-v0-1-1-y\tBACKLOG",
    ]
