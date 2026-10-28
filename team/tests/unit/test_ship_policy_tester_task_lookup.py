"""
test_ship_policy_tester_task_lookup.py
======================================
The CM ship-policy stage's TESTER-task lookup,
pgai_agent_kanban.cm.ship_policy_report_inputs.tester_task_lookup, and the
``lookup`` mode of that module's command line, which
read_ship_policy_tester_lookup (team/scripts/lib/cm/
release_ship_policy_report.sh) runs through pp_run_ops.

Coverage:
  - A TESTER task for the RC is found, with its state and report path.
  - No matching task (an empty or missing tasks directory, or only
    non-TESTER tasks) prints the not-found values.
  - A TESTER task for a different RC is neither found nor a mismatch.
  - Selection: the last DONE candidate wins over a later non-DONE one; with
    no DONE candidate the last candidate wins.
  - A DONE task without a report is found with an empty report path: the
    missing-ordered-report input the matrix requeues.
  - Through the bash reader: the stdout lines, exit 0, and the mismatch
    narration on stderr ahead of the values when both streams are merged.
  - A usage error on the module's command line exits 2.

Normalized-match and mismatch reporting through the bash reader are covered
in test_cm_ship_policy_bash.py.
"""

from __future__ import annotations

import pathlib

# Imported as a module: a bare tester_task_lookup name here would be
# collected by pytest as a test function.
from pgai_agent_kanban.cm import ship_policy_report_inputs as inputs
from tests.unit.shell_harness import run_bash

# Path to the library under test, relative to team/ (run_bash's cwd).
_LIB = "scripts/lib/cm/release_ship_policy_report.sh"

_NOT_FOUND = [
    "tester_task_dir=",
    "tester_task_id=none",
    "tester_state=none",
    "tester_report_path=",
    "tester_task_found=no",
]


def _task(
    tasks_dir: pathlib.Path,
    task_id: str,
    *,
    role: str = "TESTER",
    version: str = "v1.2.3",
    state: str = "DONE",
    with_report: bool = True,
) -> pathlib.Path:
    task_dir = tasks_dir / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "README.md").write_text(
        f"# Task: {task_id}\n\n## Role\n{role}\n\n## Release Version\n{version}\n", encoding="utf-8"
    )
    (task_dir / "status.md").write_text(f"# Status\n\n## State\n{state}\n", encoding="utf-8")
    if with_report:
        (task_dir / "artifacts").mkdir()
        (task_dir / "artifacts" / "report.md").write_text("## Recommendation\nPASS\n", encoding="utf-8")
    return task_dir


def test_matching_tester_task_is_found_with_its_state_and_report(tmp_path):
    tasks = tmp_path / "tasks"
    task_dir = _task(tasks, "TESTER-20260601-001-verify")
    result = inputs.tester_task_lookup(str(tasks), "v1.2.3")
    assert result.found
    assert result.task_dir == str(task_dir)
    assert result.state == "DONE"
    assert result.mismatches == [] and result.notes == []
    assert inputs.lookup_lines(result) == [
        f"tester_task_dir={task_dir}",
        "tester_task_id=TESTER-20260601-001-verify",
        "tester_state=DONE",
        f"tester_report_path={task_dir / 'artifacts' / 'report.md'}",
        "tester_task_found=yes",
    ]


def test_found_task_without_a_report_prints_an_empty_report_path(tmp_path):
    tasks = tmp_path / "tasks"
    _task(tasks, "TESTER-20260601-001-verify", state="WORKING", with_report=False)
    lines = inputs.lookup_lines(inputs.tester_task_lookup(str(tasks), "v1.2.3"))
    assert "tester_report_path=" in lines
    assert "tester_state=WORKING" in lines
    assert "tester_task_found=yes" in lines


def test_done_task_without_a_report_is_found_and_reads_as_a_missing_ordered_report(tmp_path):
    """A DONE TESTER task with no artifacts/report.md is the missing-ordered-
    report input: found, state DONE, empty report path -- which the matrix
    reads as verification that did not run (requeued once, never HALT), not
    as the test-not-required row."""
    tasks = tmp_path / "tasks"
    task_dir = _task(tasks, "TESTER-20260601-001-verify", state="DONE", with_report=False)
    lines = inputs.lookup_lines(inputs.tester_task_lookup(str(tasks), "v1.2.3"))
    assert lines == [
        f"tester_task_dir={task_dir}",
        "tester_task_id=TESTER-20260601-001-verify",
        "tester_state=DONE",
        "tester_report_path=",
        "tester_task_found=yes",
    ]
    decision = run_bash(
        tmp_path,
        "source scripts/lib/cm/release_ship_policy.sh && "
        "cm_ship_policy done none none no yes unknown unknown no available",
    )
    assert decision.returncode == 0, decision.stderr
    assert decision.stdout.strip() == "REQUEUE-VERIFICATION"


def test_no_matching_task_prints_the_not_found_values(tmp_path):
    tasks = tmp_path / "tasks"
    _task(tasks, "CODER-20260601-001-build", role="CODER")
    (tasks / "TESTER-20260601-002-no-readme").mkdir()
    for tasks_dir in (tasks, tmp_path / "missing"):
        result = inputs.tester_task_lookup(str(tasks_dir), "v1.2.3")
        assert not result.found
        assert inputs.lookup_lines(result) == _NOT_FOUND


def test_tester_task_for_a_different_rc_is_neither_found_nor_a_mismatch(tmp_path):
    tasks = tmp_path / "tasks"
    _task(tasks, "TESTER-20260601-001-verify", version="v1.2.2")
    result = inputs.tester_task_lookup(str(tasks), "v1.2.3")
    assert not result.found
    assert result.mismatches == [] and result.notes == []
    assert inputs.lookup_lines(result) == _NOT_FOUND


def test_last_done_candidate_wins_over_a_later_unfinished_one(tmp_path):
    tasks = tmp_path / "tasks"
    _task(tasks, "TESTER-20260601-001-first", state="DONE")
    done = _task(tasks, "TESTER-20260601-002-second", state="done")
    _task(tasks, "TESTER-20260601-003-third", state="WORKING")
    result = inputs.tester_task_lookup(str(tasks), "v1.2.3")
    assert (result.task_dir, result.state) == (str(done), "done")


def test_without_a_done_candidate_the_last_candidate_wins(tmp_path):
    tasks = tmp_path / "tasks"
    _task(tasks, "TESTER-20260601-001-first", state="BLOCKED")
    last = _task(tasks, "TESTER-20260601-002-second", state="WORKING")
    result = inputs.tester_task_lookup(str(tasks), "v1.2.3")
    assert (result.task_dir, result.state) == (str(last), "WORKING")


def test_bash_reader_prints_the_values_and_exits_zero(tmp_path):
    tasks = tmp_path / "tasks"
    task_dir = _task(tasks, "TESTER-20260601-001-verify")
    _task(tasks, "TESTER-20260601-002-other", version="v9.9.9")
    result = run_bash(tmp_path, f"source {_LIB} && read_ship_policy_tester_lookup '{tasks}' 'v1.2.3'")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"tester_task_dir={task_dir}",
        "tester_task_id=TESTER-20260601-001-verify",
        "tester_state=DONE",
        f"tester_report_path={task_dir / 'artifacts' / 'report.md'}",
        "tester_task_found=yes",
    ]
    assert result.stderr == ""


def test_bash_reader_narrates_mismatches_ahead_of_the_values_when_streams_merge(tmp_path):
    tasks = tmp_path / "tasks"
    task_dir = _task(tasks, "TESTER-20260601-001-verify", role="tester")
    result = run_bash(tmp_path, f"source {_LIB} && read_ship_policy_tester_lookup '{tasks}' 'v1.2.3' 2>&1")
    assert result.returncode == 0, result.stdout
    lines = result.stdout.splitlines()
    readme = task_dir / "README.md"
    assert lines[0] == (
        f"TESTER task TESTER-20260601-001-verify: Role 'tester' in {readme} matched TESTER only after normalization"
    )
    assert lines[1:] == [
        f"tester_task_dir={task_dir}",
        "tester_task_id=TESTER-20260601-001-verify",
        "tester_state=DONE",
        f"tester_report_path={readme.parent / 'artifacts' / 'report.md'}",
        "tester_task_found=yes",
        "mismatch_field=Role",
        "mismatch_raw=tester",
        f"mismatch_path={readme}",
    ]


def test_bash_reader_on_a_missing_tasks_directory_prints_not_found(tmp_path):
    result = run_bash(
        tmp_path, f"source {_LIB} && read_ship_policy_tester_lookup '{tmp_path / 'missing'}' 'v1.2.3'"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == _NOT_FOUND


def test_lookup_mode_missing_an_argument_is_a_usage_error(tmp_path):
    result = run_bash(
        tmp_path,
        "source scripts/lib/pp_run_ops.sh && "
        f"pp_run_ops pgai_agent_kanban.cm.ship_policy_report_inputs lookup '{tmp_path}'",
    )
    assert result.returncode == 2
    assert "active_rc" in result.stderr
