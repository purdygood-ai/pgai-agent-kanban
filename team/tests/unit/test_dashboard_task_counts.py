"""
test_dashboard_task_counts.py — ``pgai_agent_kanban.dashboard.task_counts``,
the one scan behind data.sh's task counters and the queue pane's per-role
rows.

What is proven here:

  1. Which directories count, how State and Role are read (first line of
     the section, uppercased, bold kept as written, ``CODER|CODER`` split,
     unknown and missing roles counted as CODER), and how the working task
     and the last five done tasks are chosen.
  2. ``format_task_counts`` prints data.sh's ``KEY=value`` lines in data.sh's
     order, with the empty and zero forms for a board with nothing in it.
  3. The CLI prints those lines (the call data.sh makes through
     ``pp_run_ops``) and refuses a wrong argument count.

Every tasks directory is a throwaway directory under ``tmp_path``.
"""

from __future__ import annotations

import os
import pathlib

import pytest

from pgai_agent_kanban.dashboard import task_counts
from pgai_agent_kanban.dashboard.task_counts import QUEUE_ROLES, format_task_counts, scan_task_counts


def _task(tasks: pathlib.Path, task_id: str, status: str | None, readme: str | None = None,
          mtime: int | None = None) -> None:
    task_dir = tasks / task_id
    task_dir.mkdir(parents=True)
    if status is not None:
        (task_dir / "status.md").write_text(status, encoding="utf-8")
        if mtime is not None:
            os.utime(task_dir / "status.md", (mtime, mtime))
    if readme is not None:
        (task_dir / "README.md").write_text(readme, encoding="utf-8")


def _status(state: str) -> str:
    return f"# Status\n\n## State\n\n{state}\n\n## Summary\nx\n"


def _role(role: str) -> str:
    return f"# Task\n\n## Role\n{role}\n"


def test_counts_by_state_and_role(tmp_path: pathlib.Path) -> None:
    tasks = tmp_path / "tasks"
    _task(tasks, "PM-20260101-001-a", _status("DONE"), _role("PM"))
    _task(tasks, "CODER-20260101-001-b", _status("WORKING"), _role("CODER|CODER"))
    _task(tasks, "CODER-20260101-002-c", _status("blocked"), _role("coder"))
    _task(tasks, "WRITER-20260101-001-d", _status("WAITING"), _role("WRITER"))
    _task(tasks, "TESTER-20260101-001-e", _status("WONT-DO"), _role("TESTER"))
    _task(tasks, "CM-20260101-001-f", _status("BACKLOG"), _role("CM"))
    _task(tasks, "PO-20260101-001-g", _status("FOO"), _role("PO"))
    _task(tasks, "HUMAN-20260101-001-h", "## Summary\nno state\n", None)

    counts = scan_task_counts(tasks)

    assert counts.totals == {"TOTAL": 8, "DONE": 2, "WORKING": 1, "BLOCKED": 1, "WAITING": 1, "BACKLOG": 3}
    assert counts.queues["PM"] == {"TOTAL": 1, "DONE": 1, "WORKING": 0, "BLOCKED": 0, "WAITING": 0}
    # PO (unknown role) and the README-less task are counted under CODER.
    assert counts.queues["CODER"] == {"TOTAL": 4, "DONE": 0, "WORKING": 1, "BLOCKED": 1, "WAITING": 0}
    assert counts.queues["WRITER"]["WAITING"] == 1
    assert counts.queues["TESTER"]["DONE"] == 1
    assert counts.queues["CM"]["TOTAL"] == 1


def test_skipped_directories_and_folders_without_status_are_not_counted(tmp_path: pathlib.Path) -> None:
    tasks = tmp_path / "tasks"
    for name in ("archive", "queues", "plans", "TASK-legacy"):
        _task(tasks, name, _status("WORKING"), _role("CODER"))
    _task(tasks, "CODER-20260101-001-nostatus", None, _role("CODER"))
    (tasks / "stray-file.md").write_text("## State\nWORKING\n", encoding="utf-8")

    counts = scan_task_counts(tasks)

    assert counts.totals["TOTAL"] == 0
    assert counts.working is None


def test_bold_state_is_kept_as_written_and_counts_toward_the_total_only(tmp_path: pathlib.Path) -> None:
    tasks = tmp_path / "tasks"
    _task(tasks, "CODER-20260101-001-a", "## State\n**DONE**\n", _role("CODER"))

    counts = scan_task_counts(tasks)

    assert counts.totals["TOTAL"] == 1
    assert counts.totals["DONE"] == 0
    assert counts.totals["BACKLOG"] == 1


def test_working_task_is_the_most_recently_touched_first_on_a_tie(tmp_path: pathlib.Path) -> None:
    tasks = tmp_path / "tasks"
    _task(tasks, "CODER-20260101-001-old", _status("WORKING"), _role("CODER"), mtime=1000)
    _task(tasks, "TESTER-20260101-001-new", _status("WORKING"), _role("TESTER"), mtime=2000)
    _task(tasks, "WRITER-20260101-001-tie", _status("WORKING"), _role("WRITER"), mtime=2000)

    working = scan_task_counts(tasks).working

    assert working is not None
    assert (working.task_id, working.mtime, working.role) == ("TESTER-20260101-001-new", 2000, "TESTER")


def test_last_done_lists_five_newest_first(tmp_path: pathlib.Path) -> None:
    tasks = tmp_path / "tasks"
    for index in range(7):
        _task(tasks, f"CODER-20260101-00{index}-d", _status("DONE"), _role("CODER"), mtime=1000 + index)

    last_done = scan_task_counts(tasks).last_done

    assert [task_id for task_id, _role_name in last_done] == [
        f"CODER-20260101-00{index}-d" for index in (6, 5, 4, 3, 2)
    ]


def test_format_prints_data_sh_lines_in_order(tmp_path: pathlib.Path) -> None:
    tasks = tmp_path / "tasks"
    _task(tasks, "CODER-20260101-001-w", _status("WORKING"), _role("CODER"), mtime=1234)
    _task(tasks, "PM-20260101-001-d", _status("DONE"), _role("PM"), mtime=1000)

    lines = format_task_counts(scan_task_counts(tasks)).splitlines()

    assert lines[:6] == [
        "TOTAL_TICKETS=2", "DONE_COUNT=1", "WORKING_COUNT=1",
        "BLOCKED_COUNT=0", "WAITING_COUNT=0", "BACKLOG_COUNT=0",
    ]
    assert lines[6:11] == [
        "QUEUE_PM_TOTAL=1", "QUEUE_PM_DONE=1", "QUEUE_PM_WORKING=0",
        "QUEUE_PM_BLOCKED=0", "QUEUE_PM_WAITING=0",
    ]
    queue_lines = lines[6:6 + 5 * len(QUEUE_ROLES)]
    assert [line.split("_")[1] for line in queue_lines[::5]] == list(QUEUE_ROLES)
    rest = lines[6 + 5 * len(QUEUE_ROLES):]
    assert rest[:3] == ["WORKING_TASK_ID=CODER-20260101-001-w", "WORKING_TASK_MTIME=1234", "WORKING_TASK_ROLE=CODER"]
    assert rest[3:5] == ["LAST_DONE_1=PM-20260101-001-d", "LAST_DONE_ROLE_1=PM"]
    assert rest[5:] == [line for index in range(2, 6) for line in (f"LAST_DONE_{index}=", f"LAST_DONE_ROLE_{index}=")]


def test_missing_tasks_root_formats_all_zero_and_empty_values(tmp_path: pathlib.Path) -> None:
    text = format_task_counts(scan_task_counts(tmp_path / "absent"))
    assert "TOTAL_TICKETS=0\n" in text
    assert "WORKING_TASK_ID=\nWORKING_TASK_MTIME=0\nWORKING_TASK_ROLE=\n" in text
    assert text.endswith("LAST_DONE_5=\nLAST_DONE_ROLE_5=\n")


def test_cli_prints_the_counters(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture) -> None:
    tasks = tmp_path / "tasks"
    _task(tasks, "CODER-20260101-001-a", _status("BLOCKED"), _role("CODER"))

    assert task_counts.main([str(tasks)]) == 0
    assert capsys.readouterr().out == format_task_counts(scan_task_counts(tasks))


def test_cli_refuses_a_wrong_argument_count(capsys: pytest.CaptureFixture) -> None:
    assert task_counts.main([]) == 1
    assert "usage:" in capsys.readouterr().err
