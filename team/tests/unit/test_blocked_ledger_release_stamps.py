"""
test_blocked_ledger_release_stamps.py
=====================================
The blocked-interval ledger stamps (pgai_agent_kanban.metrics.blocked_ledger)
that ride beside a ``## Block Record`` write, an operator reset, an unhalt,
and the operator close / wontdo of a task.

Coverage:
  - write_block_record (every wake block site, the per-task timeout kill
    included) opens a block-start for task:<key> carrying the record's
    reason_code, cause chain and class any, under the Active RC or the no-RC
    version; a re-block opens no second interval; the CLI ``write`` (what
    the wake's block_record_write calls) stamps the same way.
  - A record removal (the wake's terminal transition) closes the block with
    class any.
  - ops.write.reset_item on a blocked task closes its block with class human.
  - ops.write.unhalt removing a project HALT with no operator record in the
    sentinel-writes log closes halt:project with class human; with an
    ``op=halt`` record it closes with cause operator and never class human.
  - close_item / wontdo_item close the task's block with class any.
  - Every stamp is best-effort: an unwritable metrics directory leaves the
    record write, the reset and the unhalt exactly as they are without it.

Every test builds a throwaway kanban root under tmp_path and points the
project README's five sandbox variables at it; nothing touches the live
install. Function names describe behaviour under test (roles/TESTER.md
"Test Authoring Guidelines").
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

from pgai_agent_kanban.metrics.blocked_ledger import (
    NO_RC_VERSION,
    append_block_start,
    block_intervals,
)
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.write import close_item, reset_item, unhalt, wontdo_item
from pgai_agent_kanban.overwatch.block_record import (
    load_block_record,
    remove_block_record,
    write_block_record,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

# This file lives at team/tests/unit/; team/ is three parents up.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_PROJECT = "demo"
_TASK_ID = "CODER-20260101-001-example-task"
_RC = "v4.5.6"
_HALT_START = "2026-09-18T09:00:00+00:00"


# ---------------------------------------------------------------------------
# Sandbox builders
# ---------------------------------------------------------------------------


@pytest.fixture
def root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A throwaway kanban root with one project, under the five-variable sandbox."""
    kanban = tmp_path / "kanban"
    (kanban / "projects" / _PROJECT / "tasks" / "queues").mkdir(parents=True)
    (tmp_path / "scratch").mkdir()
    monkeypatch.setenv("KANBAN_ROOT", str(kanban))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(kanban))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(tmp_path / "scratch"))
    monkeypatch.delenv("PGAI_TASKS_DIR", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    return kanban


def _project_root(root: pathlib.Path) -> pathlib.Path:
    return root / "projects" / _PROJECT


def _ledger(root: pathlib.Path) -> pathlib.Path:
    return _project_root(root) / "metrics" / "blocked-intervals.jsonl"


def _records(root: pathlib.Path) -> list[dict]:
    path = _ledger(root)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _release_state(root: pathlib.Path, active_rc: str) -> None:
    (_project_root(root) / "release-state.md").write_text(
        f"# Release State\n\n## Active RC\n{active_rc}\n\n"
        "## RC Opened At\n2026-09-18T00:00:00+00:00\n\n## RC Opened By Task\nnone\n",
        encoding="utf-8",
    )


def _task(root: pathlib.Path, state: str = "BLOCKED") -> pathlib.Path:
    """A task folder with a status.md in *state*, a README and a queue line."""
    task_dir = _project_root(root) / "tasks" / _TASK_ID
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "README.md").write_text("# Task\n\n## Owner\nClaude\n\n## Role\nCODER\n", encoding="utf-8")
    status = task_dir / "status.md"
    status.write_text(
        f"# Status\n\n## Task\n{_TASK_ID}\n\n## Role\nCODER\n\n## State\n{state}\n\n"
        "## Blockers\nnone\n\n## Needs Human\nyes\n",
        encoding="utf-8",
    )
    queue = _project_root(root) / "tasks" / "queues" / "coder_backlog.md"
    queue.write_text(f"- [B] {_TASK_ID}\n", encoding="utf-8")
    return status


def _halt(root: pathlib.Path) -> pathlib.Path:
    halt = _project_root(root) / "HALT"
    halt.write_text("", encoding="utf-8")
    return halt


def _sentinel_record(root: pathlib.Path, sentinel: pathlib.Path, op: str) -> None:
    """One sentinel-writes log line, in the sandbox, naming *op* for *sentinel*."""
    log = root / "logs" / "sentinel-writes.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    fields = {
        "time": "2026-09-18T08:59:00+00:00",
        "epoch": "1789721940",
        "op": op,
        "path": str(sentinel),
        "sha256": "-",
        "test": "-",
    }
    with log.open("a", encoding="utf-8") as handle:
        handle.write("\t".join(f"{k}={v}" for k, v in fields.items()) + "\n")


# ---------------------------------------------------------------------------
# Block Record writes open the task's block
# ---------------------------------------------------------------------------


def test_per_task_timeout_block_record_opens_a_block_carrying_its_reason_code(root: pathlib.Path) -> None:
    _release_state(root, _RC)
    status = _task(root)

    record = write_block_record(status, "per_task_timeout", "CODER", ["worktree=gone"])

    [start] = _records(root)
    assert start["kind"] == "block-start"
    assert (start["version"], start["source"]) == (_RC, f"task:{_TASK_ID}")
    assert start["reason_code"] == "per_task_timeout"
    assert (start["cause"], start["class"]) == ("chain", "any")
    assert start["timestamp"] == record.last_blocked_at


def test_re_block_opens_no_second_interval(root: pathlib.Path) -> None:
    _release_state(root, _RC)
    status = _task(root)

    write_block_record(status, "per_task_timeout", "CODER")
    write_block_record(status, "zero_progress", "CODER")

    assert [r["kind"] for r in _records(root)] == ["block-start"]
    assert load_block_record(status).reason_code == "zero_progress"


def test_block_record_with_no_active_rc_lands_under_the_no_rc_version(root: pathlib.Path) -> None:
    _release_state(root, "none")
    status = _task(root)

    write_block_record(status, "worktree_create_failed", "CODER")

    [start] = _records(root)
    assert start["version"] == NO_RC_VERSION
    assert start["reason_code"] == "worktree_create_failed"


def test_block_record_cli_write_opens_the_block(root: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """The wake's block_record_write reaches the module CLI; it stamps too."""
    _release_state(root, _RC)
    status = _task(root)
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "PYTHONPATH": str(_TEAM_DIR),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "scratch"),
        },
    )
    result = subprocess.run(
        [
            "python3",
            "-m",
            "pgai_agent_kanban.overwatch.block_record",
            "write",
            str(status),
            "per_task_timeout",
            "CODER",
            "worktree=gone",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        env=env,
    )

    assert result.returncode == 0, result.stderr
    [start] = _records(root)
    assert (start["source"], start["reason_code"], start["cause"]) == (
        f"task:{_TASK_ID}",
        "per_task_timeout",
        "chain",
    )


def test_block_record_outside_the_project_layout_stamps_nothing(tmp_path: pathlib.Path) -> None:
    task_dir = tmp_path / "tasks" / _TASK_ID
    task_dir.mkdir(parents=True)
    status = task_dir / "status.md"
    status.write_text("# Status\n\n## State\nBLOCKED\n", encoding="utf-8")

    write_block_record(status, "per_task_timeout", "CODER")

    assert load_block_record(status) is not None
    assert not list(tmp_path.rglob("blocked-intervals.jsonl"))


def test_unwritable_metrics_directory_never_changes_the_block_record_write(
    root: pathlib.Path, capsys: pytest.CaptureFixture
) -> None:
    _release_state(root, _RC)
    status = _task(root)
    metrics = _project_root(root) / "metrics"
    metrics.mkdir()
    metrics.chmod(0o500)
    try:
        record = write_block_record(status, "per_task_timeout", "CODER")
    finally:
        metrics.chmod(0o755)

    assert load_block_record(status) == record
    assert _records(root) == []
    assert "[blocked-ledger] WARNING" in capsys.readouterr().err


def test_record_removal_on_a_terminal_transition_closes_the_block_with_class_any(root: pathlib.Path) -> None:
    _release_state(root, _RC)
    status = _task(root)
    write_block_record(status, "per_task_timeout", "CODER")

    assert remove_block_record(status) is True
    assert remove_block_record(status) is False

    records = _records(root)
    assert [r["kind"] for r in records] == ["block-start", "block-end"]
    assert records[1]["class"] == "any"


# ---------------------------------------------------------------------------
# reset_item: a human released the task
# ---------------------------------------------------------------------------


def test_reset_of_a_blocked_task_closes_its_block_with_class_human(root: pathlib.Path) -> None:
    _release_state(root, _RC)
    status = _task(root)
    write_block_record(status, "per_task_timeout", "CODER")

    reset_item(OpsContext(kanban_root=root), _PROJECT, _TASK_ID)

    records = _records(root)
    assert [r["kind"] for r in records] == ["block-start", "block-end"]
    end = records[1]
    assert (end["version"], end["source"], end["class"], end["reason_code"]) == (
        _RC,
        f"task:{_TASK_ID}",
        "human",
        "reset",
    )
    [block] = block_intervals(str(_ledger(root)))
    assert block["class"] == "human"
    assert "## State\nBACKLOG" in status.read_text(encoding="utf-8")


def test_reset_of_a_task_with_no_open_block_writes_nothing(root: pathlib.Path) -> None:
    _release_state(root, _RC)
    _task(root, state="DONE")

    reset_item(OpsContext(kanban_root=root), _PROJECT, _TASK_ID)

    assert _records(root) == []


def test_unwritable_ledger_never_changes_the_reset(root: pathlib.Path) -> None:
    _release_state(root, _RC)
    status = _task(root)
    write_block_record(status, "per_task_timeout", "CODER")
    metrics = _project_root(root) / "metrics"
    metrics.chmod(0o500)
    try:
        reset_item(OpsContext(kanban_root=root), _PROJECT, _TASK_ID)
    finally:
        metrics.chmod(0o755)

    assert "## State\nBACKLOG" in status.read_text(encoding="utf-8")
    assert [r["kind"] for r in _records(root)] == ["block-start"]


# ---------------------------------------------------------------------------
# unhalt: chain HALT cleared by a human, operator HALT recorded as operator
# ---------------------------------------------------------------------------


def test_unhalt_of_a_chain_halt_closes_halt_project_with_class_human(root: pathlib.Path) -> None:
    _release_state(root, _RC)
    halt = _halt(root)
    # Another sentinel's operator record must not make this HALT operator-caused.
    _sentinel_record(root, _project_root(root) / "HALT-AFTER", "halt_after")
    append_block_start(str(_ledger(root)), _RC, "halt:project", _HALT_START)

    unhalt(OpsContext(kanban_root=root), _PROJECT)

    assert not halt.exists()
    records = _records(root)
    assert [r["kind"] for r in records] == ["block-start", "block-end"]
    end = records[1]
    assert (end["version"], end["source"], end["class"], end["cause"], end["reason_code"]) == (
        _RC,
        "halt:project",
        "human",
        "chain",
        "unhalt",
    )
    [block] = block_intervals(str(_ledger(root)))
    assert (block["class"], block["cause"]) == ("human", "chain")


def test_unhalt_of_an_operator_halt_closes_with_cause_operator_never_human(root: pathlib.Path) -> None:
    _release_state(root, _RC)
    halt = _halt(root)
    _sentinel_record(root, halt, "halt")
    # Opened the way the wake's HALT adapter records an operator HALT.
    append_block_start(str(_ledger(root)), _RC, "halt:project", _HALT_START, cause="operator")

    unhalt(OpsContext(kanban_root=root), _PROJECT)

    assert not halt.exists()
    end = _records(root)[1]
    assert (end["kind"], end["cause"], end["class"]) == ("block-end", "operator", "any")
    [block] = block_intervals(str(_ledger(root)))
    assert (block["cause"], block["class"], block["resolution_class"]) == ("operator", "any", "any")


def test_unhalt_reads_operator_provenance_even_when_the_start_names_no_cause(root: pathlib.Path) -> None:
    _release_state(root, _RC)
    halt = _halt(root)
    _sentinel_record(root, halt, "halt")
    append_block_start(str(_ledger(root)), _RC, "halt:project", _HALT_START)

    unhalt(OpsContext(kanban_root=root), _PROJECT)

    end = _records(root)[1]
    assert end["cause"] == "operator"
    assert end["class"] != "human"


def test_unhalt_closes_a_halt_opened_with_no_active_rc(root: pathlib.Path) -> None:
    _release_state(root, _RC)
    _halt(root)
    append_block_start(str(_ledger(root)), NO_RC_VERSION, "halt:project", _HALT_START)

    unhalt(OpsContext(kanban_root=root), _PROJECT)

    assert [(r["kind"], r["version"]) for r in _records(root)] == [
        ("block-start", NO_RC_VERSION),
        ("block-end", NO_RC_VERSION),
    ]


def test_unhalt_with_no_halt_present_writes_nothing(root: pathlib.Path) -> None:
    _release_state(root, _RC)
    append_block_start(str(_ledger(root)), _RC, "halt:project", _HALT_START)

    unhalt(OpsContext(kanban_root=root), _PROJECT)

    assert [r["kind"] for r in _records(root)] == ["block-start"]


def test_unwritable_ledger_never_changes_the_unhalt(root: pathlib.Path) -> None:
    _release_state(root, _RC)
    halt = _halt(root)
    append_block_start(str(_ledger(root)), _RC, "halt:project", _HALT_START)
    metrics = _project_root(root) / "metrics"
    metrics.chmod(0o500)
    try:
        unhalt(OpsContext(kanban_root=root), _PROJECT)
    finally:
        metrics.chmod(0o755)

    assert not halt.exists()
    assert [r["kind"] for r in _records(root)] == ["block-start"]


# ---------------------------------------------------------------------------
# close / wontdo: the task's terminal state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("operation", "reason_code"), [(close_item, "done"), (wontdo_item, "wont-do")])
def test_operator_close_of_a_blocked_task_closes_its_block_with_class_any(
    root: pathlib.Path, operation, reason_code: str
) -> None:
    _release_state(root, _RC)
    status = _task(root)
    write_block_record(status, "per_task_timeout", "CODER")

    operation(OpsContext(kanban_root=root), _PROJECT, _TASK_ID)

    end = _records(root)[1]
    assert (end["kind"], end["class"], end["reason_code"]) == ("block-end", "any", reason_code)
