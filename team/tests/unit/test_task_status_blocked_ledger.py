"""
test_task_status_blocked_ledger.py
====================================
Unit tests for the blocked-interval ledger stamp task_status.py's
write_task_status() rides beside a status.md write -- one of the two
writers of the blocked-interval ledger
(pgai_agent_kanban.metrics.blocked_ledger).

Coverage:
  - A BLOCKED transition carrying --needs-human yes IN THE SAME CALL opens
    a block-start attributed to the project's Active RC; a later
    transition out of BLOCKED appends the matching block-end.
  - A BLOCKED transition that does not carry needs-human yes in that same
    call -- either the flag omitted, or explicitly "no" -- appends
    nothing, even though the file now reads BLOCKED.
  - With Active RC "none", no transition writes anything to the ledger.
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


def _ledger_records(tmp_path: Path, project: str) -> list[dict]:
    ledger_path = tmp_path / "kanban" / "projects" / project / "metrics" / "blocked-intervals.jsonl"
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
# Not opening: BLOCKED without needs_human="yes" in THIS call
# ---------------------------------------------------------------------------


def test_blocked_without_needs_human_flag_appends_nothing(tmp_path: Path) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_release_state(tmp_path, "demo", active_rc="v4.5.6")

    write_task_status(status_file, state="BLOCKED", project="demo", key="T-1")

    assert _ledger_records(tmp_path, "demo") == []
    assert "BLOCKED" in status_file.read_text(encoding="utf-8")


def test_blocked_with_needs_human_no_appends_nothing(tmp_path: Path) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_release_state(tmp_path, "demo", active_rc="v4.5.6")

    write_task_status(
        status_file, state="BLOCKED", needs_human="no", project="demo", key="T-1"
    )

    assert _ledger_records(tmp_path, "demo") == []


# ---------------------------------------------------------------------------
# Active RC none: no RC to attribute the interval to
# ---------------------------------------------------------------------------


def test_active_rc_none_writes_nothing_even_with_needs_human_yes(tmp_path: Path) -> None:
    status_file = _make_status_file(tmp_path, project="demo", key="T-1")
    _write_release_state(tmp_path, "demo", active_rc="none")

    write_task_status(
        status_file, state="BLOCKED", needs_human="yes", project="demo", key="T-1"
    )

    assert not (tmp_path / "kanban" / "projects" / "demo" / "metrics").exists()


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
