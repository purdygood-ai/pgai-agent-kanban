"""
test_transient_backoff.py
=========================
Transient-error requeues wait out a backoff that grows with the retry count
(``pgai_agent_kanban.overwatch.transient_backoff``, reached from bash through
``transient_backoff_due`` in scripts/lib/overwatch_protocol.sh), and both
requeue paths honour it:

  1. The window: 5, 10, then 20 minutes (capped) after the task's latest
     block for retry counts 1, 2, 3 and beyond, read from the classifier
     table; due exactly at the window's end; no Block Record means no
     window (due), as before the backoff existed. The CLI prints ``due``
     (exit 0) or ``not-before <ISO>`` (exit 3).
  2. OVERWATCH's check-transient-api-error.sh leaves a task inside its
     window exactly as found (a ``transient-requeue-backoff`` row is logged)
     and requeues it once the window has passed; a task at the ceiling
     still escalates at once, whatever the window.
  3. The wake's recheck_blocked_tasks auto-retry waits for the window too.

Every fixture builds a sandbox kanban root under pytest's tmp_path; nothing
reads or writes the live install or a real provider.

Naming: function names describe behaviour under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import datetime
import json
import pathlib
import shlex
import subprocess
import textwrap

import pytest

from pgai_agent_kanban.overwatch import transient_backoff as tb
from pgai_agent_kanban.overwatch.block_record import write_block_record
from pgai_agent_kanban.task_status import read_section
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

# This file lives at team/tests/unit/; team/ is three parents up.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_LIB = _TEAM_DIR / "scripts" / "lib"
_PROJECT = "transient-proj"
_TASK = "CODER-20260101-001-example-task"
_T0 = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _section(text: str, heading: str) -> str:
    return (read_section(text, heading) or "").strip()


def _retry_record(count: int) -> str:
    return (
        "## Transient Retry\nreason_code: transient_api_error\n"
        f"retry_count: {count}\nretry_limit: 3\nprovider: claude\nlast_error: API Error: 529 overloaded\n\n"
    )


def _status_file(directory: pathlib.Path, count: int, *, blocked_at: "datetime.datetime | None") -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    status = directory / "status.md"
    status.write_text(
        f"# Status\n\n## Task\n{_TASK}\n\n## State\nBLOCKED\n\n## Summary\nBlocked.\n\n"
        "## Blockers\nTRANSIENT API ERROR\n\n## Blocked Reason\nTRANSIENT API ERROR\n\n## Needs Human\nno\n\n"
        + _retry_record(count),
        encoding="utf-8",
    )
    if blocked_at is not None:
        write_block_record(status, "transient_api_error", "CODER", ["batch_log=none"], now=blocked_at)
    return status


# ---------------------------------------------------------------------------
# 1. The window
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("count", "minutes"), [(0, 5), (1, 5), (2, 10), (3, 20), (4, 20), (9, 20)])
def test_backoff_window_grows_five_ten_twenty_minutes_and_caps(count, minutes):
    assert tb.backoff_window_s(count) == minutes * 60


@pytest.mark.parametrize(("count", "minutes"), [(1, 5), (2, 10), (3, 20)])
def test_requeue_is_not_due_inside_the_window_and_due_at_its_end(tmp_path, count, minutes):
    status = _status_file(tmp_path / "task", count, blocked_at=_T0)
    end = _T0 + datetime.timedelta(minutes=minutes)
    assert tb.requeue_due(status, now=end - datetime.timedelta(seconds=1)) == (False, end.isoformat())
    assert tb.requeue_due(status, now=end) == (True, end.isoformat())


def test_requeue_without_a_block_record_is_due_at_once(tmp_path):
    status = _status_file(tmp_path / "task", 2, blocked_at=None)
    assert tb.requeue_due(status, now=_T0) == (True, None)
    assert tb.requeue_due(tmp_path / "missing" / "status.md", now=_T0) == (True, None)


def test_backoff_cli_prints_due_or_not_before_with_its_exit_code(tmp_path, capsys):
    status = _status_file(tmp_path / "task", 1, blocked_at=_T0)
    inside = (_T0 + datetime.timedelta(minutes=4)).isoformat()
    after = (_T0 + datetime.timedelta(minutes=6)).isoformat()
    assert tb.main(["due", str(status), "--now", inside]) == tb.EXIT_NOT_YET
    assert capsys.readouterr().out == f"not-before {(_T0 + datetime.timedelta(minutes=5)).isoformat()}\n"
    assert tb.main(["due", str(status), "--now", after]) == tb.EXIT_DUE
    assert capsys.readouterr().out == "due\n"


def test_backoff_cli_help_names_its_arguments(capsys):
    with pytest.raises(SystemExit) as exit_info:
        tb.main(["due", "--help"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "STATUS_FILE" in out and "--now" in out and "--table" in out


# ---------------------------------------------------------------------------
# 2. OVERWATCH's transient check
# ---------------------------------------------------------------------------


class Sandbox:
    """A sandbox kanban root with one project and one transient-blocked task."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.root = tmp_path / "kanban"
        self.project_root = self.root / "projects" / _PROJECT
        self.task_dir = self.project_root / "tasks" / _TASK
        self.queue = self.project_root / "tasks" / "queues" / "coder_backlog.md"
        self.batch_log = self.root / "logs" / "agents" / "coder-batch-20260101-000000.log"
        self.home = tmp_path / "home"
        for d in (self.project_root / "overwatch" / "backups", self.project_root / "bugs", self.task_dir / "logs",
                  self.queue.parent, self.batch_log.parent, self.root / "tmp", self.home):
            d.mkdir(parents=True, exist_ok=True)
        (self.project_root / "overwatch" / "actions.log").write_text("", encoding="utf-8")
        (self.project_root / "project.cfg").write_text(f"[project]\nproject_name = {_PROJECT}\n", encoding="utf-8")
        (self.root / "kanban.cfg").write_text("[wake]\ntransient_retry_cap = 3\n", encoding="utf-8")
        self.queue.write_text(f"# CODER Backlog\n\n- [B] {_TASK} — example\n", encoding="utf-8")
        (self.task_dir / "README.md").write_text("# Task\n\n## Role\nCODER\n\n## Prerequisites\nnone\n",
                                                 encoding="utf-8")
        preamble, span = "wake starting\n", "agent working\nAPI Error: 529 overloaded_error\n"
        self.batch_log.write_text(preamble + span, encoding="utf-8")
        ledger = {"event": "returned", "batch_log": str(self.batch_log), "offset_start": len(preamble),
                  "offset_end": len(preamble) + len(span), "exit_code": 1, "recorded_at": "2026-01-01T10:00:00Z"}
        (self.task_dir / "logs" / "dispatch.jsonl").write_text(json.dumps(ledger) + "\n", encoding="utf-8")

    @property
    def status(self) -> pathlib.Path:
        return self.task_dir / "status.md"

    @property
    def actions(self) -> str:
        return (self.project_root / "overwatch" / "actions.log").read_text(encoding="utf-8")

    def seed(self, count: int, blocked_minutes_ago: int) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        _status_file(self.task_dir, count, blocked_at=now - datetime.timedelta(minutes=blocked_minutes_ago))

    def bash(self, body: str) -> subprocess.CompletedProcess:
        env = build_scrubbed_env(home_dir=self.home, extra_env={
            "KANBAN_ROOT": str(self.root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
            "OVERWATCH_PROJECT": _PROJECT,
            "HALT_OVERWATCH": "",
        })
        script = f"set -euo pipefail\ncd {shlex.quote(str(_TEAM_DIR))}\n" + body
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=180, env=env)

    def run_transient_check(self) -> subprocess.CompletedProcess:
        result = self.bash(textwrap.dedent(f"""\
            source scripts/lib/overwatch_protocol.sh
            overwatch_halt_first_fix() {{ "$1"; }}
            source scripts/lib/overwatch-checks/check-transient-api-error.sh
            rc=0
            overwatch_check_transient_api_error || rc=$?
            echo "CHECK_EXIT=$rc"
            """))
        assert "CHECK_EXIT=0" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"
        return result


@pytest.fixture
def sandbox(tmp_path: pathlib.Path) -> Sandbox:
    return Sandbox(tmp_path)


@pytest.mark.parametrize(("count", "blocked_minutes_ago"), [(1, 2), (2, 7), (2, 9)])
def test_transient_check_leaves_a_task_inside_its_backoff_window_untouched(sandbox, count, blocked_minutes_ago):
    sandbox.seed(count, blocked_minutes_ago)
    before = sandbox.status.read_bytes()

    result = sandbox.run_transient_check()

    assert sandbox.status.read_bytes() == before
    assert f"- [B] {_TASK}" in sandbox.queue.read_text(encoding="utf-8")
    assert "backoff" in result.stderr and "not before" in result.stderr
    assert "transient-requeue-backoff" in sandbox.actions and "transient-auto-requeued" not in sandbox.actions


@pytest.mark.parametrize(("count", "blocked_minutes_ago"), [(1, 6), (2, 11)])
def test_transient_check_requeues_once_the_backoff_window_has_passed(sandbox, count, blocked_minutes_ago):
    sandbox.seed(count, blocked_minutes_ago)

    sandbox.run_transient_check()

    text = sandbox.status.read_text(encoding="utf-8")
    assert _section(text, "State") == "BACKLOG", text
    assert f"retry_count: {count + 1}" in _section(text, "Transient Retry")
    assert "transient-auto-requeued" in sandbox.actions


def test_transient_check_escalates_at_the_ceiling_whatever_the_window(sandbox):
    sandbox.seed(3, 0)

    sandbox.run_transient_check()

    text = sandbox.status.read_text(encoding="utf-8")
    assert _section(text, "State") == "BLOCKED" and _section(text, "Needs Human") == "yes"
    assert "transient-ceiling-needs-human-escalated" in sandbox.actions
    assert "transient-requeue-backoff" not in sandbox.actions


# ---------------------------------------------------------------------------
# 3. The wake's auto-retry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("blocked_minutes_ago", "promoted"), [(1, False), (6, True)])
def test_wake_auto_retry_waits_for_the_backoff_window(sandbox, blocked_minutes_ago, promoted):
    sandbox.seed(1, blocked_minutes_ago)

    result = sandbox.bash(textwrap.dedent(f"""\
        log() {{ echo "LOG: $*"; }}
        get_release_state_field() {{ echo none; }}
        source scripts/lib/pp_run_ops.sh
        source scripts/lib/overwatch_protocol.sh
        source scripts/lib/wake_task_state.sh
        kanban_task_id_pattern() {{ echo '^[A-Z]+-[0-9]{{8}}-[0-9]{{3}}-[a-z0-9-]+$'; }}
        BACKLOG={shlex.quote(str(sandbox.queue))}
        TASKS_ROOT={shlex.quote(str(sandbox.task_dir.parent))}
        recheck_blocked_tasks
        """))

    assert result.returncode == 0, result.stderr
    state = _section(sandbox.status.read_text(encoding="utf-8"), "State")
    if promoted:
        assert state == "BACKLOG" and "scheduling auto-retry" in result.stdout, result.stdout
    else:
        assert state == "BLOCKED", result.stdout
        assert "waits for its backoff window, not before" in result.stdout, result.stdout
