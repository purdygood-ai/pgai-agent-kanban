"""
test_task_status_blocked_ledger.py
====================================
Unit tests for the blocked-interval ledger stamp task_status.py's
write_task_status() rides beside a status.md write -- one of the two
writers of the blocked-interval ledger
(pgai_agent_kanban.metrics.blocked_ledger).

Coverage:
  - A BLOCKED write opens a block-start for task:<key> attributed to the
    project's Active RC, whatever its --needs-human value, carrying the
    task's ## Block Record reason_code, class any and cause chain; a later
    transition out of BLOCKED appends the matching block-end.
  - --needs-human yes on a non-terminal state (WORKING) opens a block with
    reason_code needs_human; on DONE or WONT-DO it opens none.
  - DONE or WONT-DO closes the open block with class any; a move out of
    BLOCKED that still passes --needs-human yes keeps it open.
  - A block already open (a repeated BLOCKED write, or one a Block Record
    write opened) is never opened twice.
  - With Active RC "none" the block is recorded under the ledger's no-RC
    version, and blocked_minutes() for a real RC is unchanged.
  - A library caller that omits project/key (the pre-existing call shape)
    gets no ledger stamp at all -- the existing contract is untouched.
  - An unwritable metrics directory never raises out of write_task_status:
    status.md is still written and the call still returns normally.
  - The status.md write's own outcome (byte content, no exception) is
    identical whether or not the ledger stamp succeeds -- the stamp rides
    beside the write, never gates it.

All tests use tmp_path; no live filesystem paths. Function names describe
behaviour under test, never bug/task IDs (roles/TESTER.md "Test Authoring
Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pgai_agent_kanban.metrics.blocked_ledger import (
    NO_RC_VERSION,
    append_block_end,
    append_block_start,
    block_intervals,
    blocked_minutes,
)
from pgai_agent_kanban.overwatch.block_record import load_block_record, write_block_record
from pgai_agent_kanban.task_status import write_task_status

_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_TEMPLATE_PATH = _TEAM_DIR / "templates" / "task" / "task-status" / "status.md"


def _template_text() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def _make_status_file(tmp_path: Path, project: str = "demo", key: str = "T-1") -> Path:
    """Write the canonical template into a fresh task folder and return its path.

    Mirrors test_task_status.py's own fixture builder exactly (same kanban
    root layout: tmp_path/kanban/projects/<project>/tasks/<key>/status.md),
    so a release-state.md dropped in the sibling project directory resolves
    through the same OpsContext the write path uses.
    """
    status_file = tmp_path / "kanban" / "projects" / project / "tasks" / key / "status.md"
    status_file.parent.mkdir(parents=True, exist_ok=True)
    status_file.write_text(_template_text(), encoding="utf-8")
    return status_file


def _write_release_state(tmp_path: Path, project: str, active_rc: str) -> None:
    release_state = tmp_path / "kanban" / "projects" / project / "release-state.md"
    release_state.parent.mkdir(parents=True, exist_ok=True)
    release_state.write_text(
        "# Release State\n\n"
        "## Active RC\n"
        f"{active_rc}\n\n"
        "## RC Opened At\n"
        "2026-09-18T00:00:00+00:00\n\n"
        "## RC Opened By Task\n"
        "none\n",
        encoding="utf-8",
    )


def _ledger_path(tmp_path: Path, project: str) -> Path:
    return tmp_path / "kanban" / "projects" / project / "metrics" / "blocked-intervals.jsonl"


def _write_role(status_file: Path, role: str = "CODER") -> None:
    """A task README naming an owner role, so a BLOCKED write records a Block Record."""
    (status_file.parent / "README.md").write_text(f"# Task\n\n## Role\n{role}\n", encoding="utf-8")


def _ledger_records(tmp_path: Path, project: str) -> list[dict]:
    ledger_path = _ledger_path(tmp_path, project)
    if not ledger_path.is_file():
        return []
    text = ledger_path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@pytest.fixture(autouse=True)
def _resolve_kanban_root_to_tmp_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point OpsContext.from_env() at tmp_path/kanban for every test in this file.

    Same override _run_cli uses in test_task_status.py: PGAI_AGENT_KANBAN_ROOT_PATH
    set, legacy KANBAN_ROOT cleared so it cannot take precedence over the
    sandboxed root.
    """
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(tmp_path / "kanban"))
    monkeypatch.delenv("KANBAN_ROOT", raising=False)
    # The rest of the project README's five-variable sandbox.
    monkeypatch.delenv("PGAI_TASKS_DIR", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    (tmp_path / "scratch").mkdir(exist_ok=True)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(tmp_path / "scratch"))


# ---------------------------------------------------------------------------
# Opening: BLOCKED + needs_human="yes" in the same call
# ---------------------------------------------------------------------------


def test_blocked_with_needs_human_yes_opens_a_block_start(tmp_path: Path) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_release_state(tmp_path, "demo", active_rc="v4.5.6")

    write_task_status(
        status_file,
        state="BLOCKED",
        needs_human="yes",
        blockers="waiting on X",
        project="demo",
        key="T-1",
    )

    records = _ledger_records(tmp_path, "demo")
    assert [r["kind"] for r in records] == ["block-start"]
    assert records[0]["version"] == "v4.5.6"
    assert records[0]["source"] == "task:T-1"


def test_move_out_of_blocked_closes_the_open_interval(tmp_path: Path) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_release_state(tmp_path, "demo", active_rc="v4.5.6")

    write_task_status(
        status_file, state="BLOCKED", needs_human="yes", project="demo", key="T-1"
    )
    write_task_status(status_file, state="WORKING", project="demo", key="T-1")

    records = _ledger_records(tmp_path, "demo")
    assert [r["kind"] for r in records] == ["block-start", "block-end"]


# ---------------------------------------------------------------------------
# Opening: BLOCKED for any reason, whatever this call's needs_human
# ---------------------------------------------------------------------------


def test_blocked_without_needs_human_flag_opens_a_block_with_the_block_record_reason_code(
    tmp_path: Path,
) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_role(status_file)
    _write_release_state(tmp_path, "demo", active_rc="v4.5.6")

    write_task_status(status_file, state="BLOCKED", project="demo", key="T-1")

    record = load_block_record(status_file)
    assert record is not None
    records = _ledger_records(tmp_path, "demo")
    assert [r["kind"] for r in records] == ["block-start"]
    start = records[0]
    assert (start["version"], start["source"]) == ("v4.5.6", "task:T-1")
    assert start["reason_code"] == record.reason_code == "agent_unclassified"
    assert (start["class"], start["cause"]) == ("any", "chain")
    assert "BLOCKED" in status_file.read_text(encoding="utf-8")


def test_blocked_with_a_named_reason_code_opens_a_block_carrying_it(tmp_path: Path) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_role(status_file)
    _write_release_state(tmp_path, "demo", active_rc="v4.5.6")

    write_task_status(
        status_file,
        state="BLOCKED",
        reason_code="agent_operator_data_action",
        action_args=["verb=restamp-fixed-in", "project=demo", "version=v4.5.6", "bugs=BUG-0001"],
        project="demo",
        key="T-1",
    )

    [start] = _ledger_records(tmp_path, "demo")
    assert start["reason_code"] == "agent_operator_data_action"


def test_blocked_with_needs_human_no_still_opens_a_block(tmp_path: Path) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_release_state(tmp_path, "demo", active_rc="v4.5.6")

    write_task_status(
        status_file, state="BLOCKED", needs_human="no", project="demo", key="T-1"
    )

    records = _ledger_records(tmp_path, "demo")
    assert [r["kind"] for r in records] == ["block-start"]
    assert records[0]["source"] == "task:T-1"


def test_repeated_blocked_writes_open_one_interval(tmp_path: Path) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_role(status_file)
    _write_release_state(tmp_path, "demo", active_rc="v4.5.6")

    write_task_status(status_file, state="BLOCKED", project="demo", key="T-1")
    write_task_status(status_file, state="BLOCKED", needs_human="yes", project="demo", key="T-1")

    assert [r["kind"] for r in _ledger_records(tmp_path, "demo")] == ["block-start"]


def test_block_record_write_then_blocked_status_write_leave_one_open_interval(
    tmp_path: Path,
) -> None:
    """A wake block site writes the Block Record; the status writer then
    writes BLOCKED for the same task. The two writers open one block."""
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_role(status_file)
    _write_release_state(tmp_path, "demo", active_rc="v4.5.6")

    write_block_record(status_file, "per_task_timeout", "CODER")
    write_task_status(status_file, state="BLOCKED", needs_human="yes", project="demo", key="T-1")

    blocks = block_intervals(str(_ledger_path(tmp_path, "demo")))
    assert len(blocks) == 1
    assert blocks[0]["end"] is None
    assert blocks[0]["reason_code"] == "per_task_timeout"


# ---------------------------------------------------------------------------
# Opening: Needs Human yes on a non-terminal state; never on a terminal one
# ---------------------------------------------------------------------------


def test_working_task_given_needs_human_yes_opens_a_block(tmp_path: Path) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_release_state(tmp_path, "demo", active_rc="v4.5.6")

    write_task_status(
        status_file, state="WORKING", needs_human="yes", project="demo", key="T-1"
    )

    [start] = _ledger_records(tmp_path, "demo")
    assert (start["kind"], start["source"], start["version"]) == (
        "block-start",
        "task:T-1",
        "v4.5.6",
    )
    assert start["reason_code"] == "needs_human"
    assert (start["class"], start["cause"]) == ("any", "chain")


@pytest.mark.parametrize("terminal_state", ["DONE", "WONT-DO"])
def test_terminal_task_given_needs_human_yes_opens_no_block(
    tmp_path: Path, terminal_state: str
) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_release_state(tmp_path, "demo", active_rc="v4.5.6")

    write_task_status(
        status_file, state=terminal_state, needs_human="yes", project="demo", key="T-1"
    )

    assert _ledger_records(tmp_path, "demo") == []
    assert terminal_state in status_file.read_text(encoding="utf-8")


def test_working_task_without_needs_human_opens_nothing(tmp_path: Path) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_release_state(tmp_path, "demo", active_rc="v4.5.6")

    write_task_status(status_file, state="WORKING", project="demo", key="T-1")
    write_task_status(status_file, state="WORKING", needs_human="no", project="demo", key="T-1")

    assert _ledger_records(tmp_path, "demo") == []


# ---------------------------------------------------------------------------
# Closing: terminal state, or a move out of BLOCKED, with class any
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("terminal_state", ["DONE", "WONT-DO"])
def test_blocked_task_reaching_a_terminal_state_closes_its_block_with_class_any(
    tmp_path: Path, terminal_state: str
) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_role(status_file)
    _write_release_state(tmp_path, "demo", active_rc="v4.5.6")

    write_task_status(status_file, state="BLOCKED", project="demo", key="T-1")
    # WONT-DO carries Needs Human yes by convention; it still only closes.
    write_task_status(
        status_file, state=terminal_state, needs_human="yes", project="demo", key="T-1"
    )

    records = _ledger_records(tmp_path, "demo")
    assert [r["kind"] for r in records] == ["block-start", "block-end"]
    end = records[1]
    assert (end["version"], end["source"], end["class"]) == ("v4.5.6", "task:T-1", "any")
    assert end["reason_code"] == terminal_state.lower()
    [block] = block_intervals(str(_ledger_path(tmp_path, "demo")))
    assert (block["class"], block["resolution_class"]) == ("any", "any")


def test_needs_human_block_on_a_working_task_closes_at_its_terminal_state(
    tmp_path: Path,
) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_release_state(tmp_path, "demo", active_rc="v4.5.6")

    write_task_status(status_file, state="WORKING", needs_human="yes", project="demo", key="T-1")
    write_task_status(status_file, state="DONE", needs_human="no", project="demo", key="T-1")

    assert [r["kind"] for r in _ledger_records(tmp_path, "demo")] == ["block-start", "block-end"]


def test_leaving_blocked_with_needs_human_yes_keeps_the_block_open(tmp_path: Path) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_role(status_file)
    _write_release_state(tmp_path, "demo", active_rc="v4.5.6")

    write_task_status(status_file, state="BLOCKED", project="demo", key="T-1")
    write_task_status(status_file, state="WORKING", needs_human="yes", project="demo", key="T-1")

    assert [r["kind"] for r in _ledger_records(tmp_path, "demo")] == ["block-start"]


def test_block_opened_before_an_rc_opened_closes_under_its_own_version(
    tmp_path: Path,
) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_role(status_file)
    _write_release_state(tmp_path, "demo", active_rc="none")
    write_task_status(status_file, state="BLOCKED", project="demo", key="T-1")

    _write_release_state(tmp_path, "demo", active_rc="v4.5.6")
    write_task_status(status_file, state="DONE", project="demo", key="T-1")

    records = _ledger_records(tmp_path, "demo")
    assert [(r["kind"], r["version"]) for r in records] == [
        ("block-start", NO_RC_VERSION),
        ("block-end", NO_RC_VERSION),
    ]


def test_block_open_under_no_rc_is_not_reopened_under_a_later_rc(tmp_path: Path) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_role(status_file)
    _write_release_state(tmp_path, "demo", active_rc="none")
    write_task_status(status_file, state="BLOCKED", project="demo", key="T-1")

    _write_release_state(tmp_path, "demo", active_rc="v4.5.6")
    write_task_status(status_file, state="BLOCKED", needs_human="yes", project="demo", key="T-1")
    write_block_record(status_file, "zero_progress", "CODER")

    open_blocks = [
        b for b in block_intervals(str(_ledger_path(tmp_path, "demo"))) if b["end"] is None
    ]
    assert [(b["version"], b["source"]) for b in open_blocks] == [(NO_RC_VERSION, "task:T-1")]


# ---------------------------------------------------------------------------
# Active RC none: recorded under the no-RC version, never a real RC's minutes
# ---------------------------------------------------------------------------


def test_active_rc_none_records_the_block_under_the_no_rc_version(tmp_path: Path) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_role(status_file)
    _write_release_state(tmp_path, "demo", active_rc="none")

    write_task_status(
        status_file, state="BLOCKED", needs_human="yes", project="demo", key="T-1"
    )

    [start] = _ledger_records(tmp_path, "demo")
    assert (start["version"], start["source"], start["kind"]) == (
        NO_RC_VERSION,
        "task:T-1",
        "block-start",
    )
    assert start["reason_code"] == "agent_unclassified"


def test_no_rc_task_block_leaves_halt_only_blocked_minutes_unchanged(tmp_path: Path) -> None:
    """A real RC's blocked minutes from HALT-only history read the same
    before and after a task block is recorded with no Active RC."""
    ledger = str(_ledger_path(tmp_path, "demo"))
    append_block_start(ledger, "v4.5.6", "halt:project", "2026-09-18T09:15:00+00:00")
    append_block_end(ledger, "v4.5.6", "halt:project", "2026-09-18T09:55:00+00:00")
    window = ("2026-09-18T00:00:00+00:00", "2099-01-01T00:00:00+00:00")
    before = blocked_minutes(ledger, "v4.5.6", *window)

    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_role(status_file)
    _write_release_state(tmp_path, "demo", active_rc="none")
    write_task_status(status_file, state="BLOCKED", project="demo", key="T-1")

    assert before == 40.0
    assert blocked_minutes(ledger, "v4.5.6", *window) == before
    assert [r["version"] for r in _ledger_records(tmp_path, "demo")][-1] == NO_RC_VERSION


# ---------------------------------------------------------------------------
# Existing contract untouched: a caller that omits project/key gets no stamp
# ---------------------------------------------------------------------------


def test_omitting_project_and_key_skips_the_ledger_entirely(tmp_path: Path) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_release_state(tmp_path, "demo", active_rc="v4.5.6")

    # No project=/key= kwargs at all -- the call shape of every caller
    # that does not opt in to the ledger.
    write_task_status(status_file, state="BLOCKED", needs_human="yes")

    assert not (tmp_path / "kanban" / "projects" / "demo" / "metrics").exists()
    assert "BLOCKED" in status_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Best-effort: a ledger failure never affects the status.md write's outcome
# ---------------------------------------------------------------------------


def test_unwritable_metrics_directory_still_writes_status_and_returns(
    tmp_path: Path,
) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_release_state(tmp_path, "demo", active_rc="v4.5.6")

    metrics_dir = tmp_path / "kanban" / "projects" / "demo" / "metrics"
    metrics_dir.mkdir(parents=True)
    metrics_dir.chmod(0o000)
    try:
        # Must not raise -- the status.md write already succeeded by the
        # time the ledger stamp is attempted.
        write_task_status(
            status_file,
            state="BLOCKED",
            needs_human="yes",
            blockers="x",
            project="demo",
            key="T-1",
        )
    finally:
        metrics_dir.chmod(0o755)

    assert "BLOCKED" in status_file.read_text(encoding="utf-8")


def test_ledger_stamp_failure_leaves_status_write_byte_identical_to_success(
    tmp_path: Path,
) -> None:
    """The status.md bytes written are identical regardless of whether the
    ledger stamp beside it succeeds or silently fails -- the stamp rides
    beside the write and never influences it."""
    ok_file = _make_status_file(tmp_path, project="ok-demo", key="T-1")
    _write_release_state(tmp_path, "ok-demo", active_rc="v4.5.6")
    write_task_status(
        ok_file, state="BLOCKED", needs_human="yes", project="ok-demo", key="T-1"
    )

    fail_file = _make_status_file(tmp_path, project="fail-demo", key="T-1")
    _write_release_state(tmp_path, "fail-demo", active_rc="v4.5.6")
    fail_metrics_dir = tmp_path / "kanban" / "projects" / "fail-demo" / "metrics"
    fail_metrics_dir.mkdir(parents=True)
    fail_metrics_dir.chmod(0o000)
    try:
        write_task_status(
            fail_file, state="BLOCKED", needs_human="yes", project="fail-demo", key="T-1"
        )
    finally:
        fail_metrics_dir.chmod(0o755)

    assert ok_file.read_bytes() == fail_file.read_bytes()
