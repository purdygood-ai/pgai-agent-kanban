"""
test_dispatch_hold_systemic_blocks.py
=====================================
Sandbox wakes for the blocked-task guard in run_project_chain
(team/scripts/lib/wake_driver.sh): a project's task dispatch is held only
while its human-gated blocks (BLOCKED, Needs Human: yes) are systemic, never
for OVERWATCH, and the WAITING/BLOCKED re-checks run on a held wake too.

Each test drives the real production entry point -- a
`bash scripts/wake/claude.sh --agent=<agent>` subprocess -- against a
throwaway kanban root built under pytest's tmp_path: a full copy of this dev
tree's scripts/, pgai_agent_kanban/, workflows/, pm-agent/ and config/ (the
set install.sh mirrors), one document-workflow project (git_mode=none, so no
dev tree, worktree or real repository is involved) and a PATH-shadowing
`claude` stub (tests/fixtures/claude_provider_stub.py) that marks a
dispatched task DONE. No real provider, network or token is reached, and the
subprocess environment is built from an explicit set
(tests/fixtures/scrubbed_subprocess_env.py) with every live-install name
pointed into the sandbox.

Behaviours:
  - one task-local human-gated block does not stop an unrelated BACKLOG
    CODER task from being dispatched;
  - two human-gated blocks with the same reason_code inside
    [wake] block_storm_window, measured back from the wake's own clock,
    hold dispatch, naming the count, the reason_code and when the hold
    lifts; the same two blocks further apart than the window do not;
  - one block whose reason_code is on the systemic list holds dispatch on
    its own;
  - AGENT=overwatch is never held;
  - recheck_waiting_tasks and recheck_blocked_tasks run on a held wake: a
    WAITING task whose prerequisite is DONE returns to BACKLOG, and a
    transient-error BLOCKED task whose backoff has passed is promoted.
"""

from __future__ import annotations

import datetime
import pathlib
import shutil
import subprocess
import sys

from pgai_agent_kanban.overwatch.block_record import write_block_record
from tests.fixtures.claude_provider_stub import call_count, install_claude_provider_stub
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_MIRRORED_DIRS = ("scripts", "pgai_agent_kanban", "workflows", "pm-agent", "config")
_PROJECT = "hold-probe"
_AGENTS = ("pm", "coder", "writer", "tester", "cm", "overwatch")
_WINDOW_S = 3600
_WAKE_TIMEOUT_S = 240

_BLOCKED_A = "CODER-20260101-001-held-blocker-a"
_BLOCKED_B = "WRITER-20260101-002-held-blocker-b"
_UNRELATED = "CODER-20260101-003-unrelated-work"
_DONE_PREREQ = "CODER-20260101-004-finished-prerequisite"
_WAITING = "CODER-20260101-005-waiting-on-finished"
_TRANSIENT = "CODER-20260101-006-transient-error"

_HELD = "not dispatching new tasks"
_TASK_LOCAL = "dispatching (a task-local block holds only its dependents)"
_OVERWATCH_NOT_HELD = "overwatch is never held, dispatching"
_HOLD_ERROR_KEY = "wake.dispatch_held_systemic_block"

#: A block time long past every backoff window.
_LONG_AGO = datetime.datetime(2026, 1, 1, 10, 0, tzinfo=datetime.timezone.utc)



class _Sandbox:
    """A throwaway kanban root with one document-workflow project."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.tmp_path = tmp_path
        self.root = tmp_path / "kanban"
        self.project = self.root / "projects" / _PROJECT
        self.tasks = self.project / "tasks"
        self.queues = self.tasks / "queues"
        self.control_dir = tmp_path / "stub-control"
        self.bin_dir = tmp_path / "stub-bin"
        self._build()

    def _build(self) -> None:
        scratch = self.tmp_path / "scratch"
        scratch.mkdir(parents=True)
        self.queues.mkdir(parents=True)
        (self.root / "logs").mkdir(parents=True)
        (self.root / "locks").mkdir(parents=True)
        (self.root / "kanban.cfg").write_text(
            "[paths]\n"
            f"tmp_root = {scratch}\n"
            "tmp_subdir = pgai_kanban_tmp\n\n"
            "[chain]\npm_mode = manual\n\n"
            "[wake]\n"
            "max_tasks_per_wake = 3\n"
            f"block_storm_window = {_WINDOW_S}\n",
            encoding="utf-8",
        )
        (self.root / "projects.cfg").write_text(
            f"[project:{_PROJECT}]\npriority=1\nenabled=true\n", encoding="utf-8"
        )
        (self.project / "project.cfg").write_text(
            f"[project]\nproject_name = {_PROJECT}\nworkflow_type = document\n",
            encoding="utf-8",
        )
        for agent in _AGENTS:
            (self.queues / f"{agent}_backlog.md").write_text(
                f"# {agent} backlog\n\n", encoding="utf-8"
            )
        for name in _MIRRORED_DIRS:
            shutil.copytree(
                _TEAM_DIR / name,
                self.root / name,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        install_claude_provider_stub(self.bin_dir, self.control_dir)

    # --- tasks -------------------------------------------------------------

    def add_task(
        self,
        task_id: str,
        *,
        state: str,
        needs_human: str = "no",
        prerequisites: str = "none",
        extra_status: str = "",
    ) -> pathlib.Path:
        """Queue *task_id* in its role's backlog and write README + status."""
        role = task_id.split("-", 1)[0]
        task_dir = self.tasks / task_id
        task_dir.mkdir(parents=True)
        (task_dir / "README.md").write_text(
            f"# Task: {task_id}\n\n"
            f"## Task ID\n{task_id}\n\n"
            f"## Role\n{role}\n\n"
            "## Workflow Type\ndocument\n\n"
            "## Git Repo\nnone\n\n"
            "## Working Directory\nnone\n\n"
            "## Source Branch\nnone\n\n"
            f"## Prerequisites\n{prerequisites}\n",
            encoding="utf-8",
        )
        status = task_dir / "status.md"
        status.write_text(
            f"# Status\n\n## Task\n{task_id}\n\n## State\n{state}\n\n"
            "## Summary\nnone\n\n"
            f"## Blockers\nnone\n\n## Needs Human\n{needs_human}\n{extra_status}",
            encoding="utf-8",
        )
        marker = {"BLOCKED": "B", "WAITING": "W", "DONE": "x"}.get(state, " ")
        queue = self.queues / f"{role.lower()}_backlog.md"
        with queue.open("a", encoding="utf-8") as handle:
            handle.write(f"- [{marker}] {task_id} — fixture task\n")
        return status

    def add_human_gated_block(self, task_id: str, reason_code: str, minutes_ago: int) -> None:
        """A BLOCKED, Needs Human: yes task whose Block Record names
        *reason_code*, blocked *minutes_ago* minutes before now -- the wake
        measures the storm window and the hold cap back from its own clock."""
        status = self.add_task(task_id, state="BLOCKED", needs_human="yes")
        write_block_record(
            status,
            reason_code,
            task_id.split("-", 1)[0],
            ["worktree=gone"],
            now=datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
            - datetime.timedelta(minutes=minutes_ago),
        )

    def state(self, task_id: str) -> str:
        text = (self.tasks / task_id / "status.md").read_text(encoding="utf-8")
        lines = text.split("## State", 1)[1].strip().splitlines()
        return lines[0].strip()

    # --- the wake ------------------------------------------------------------

    def wake(self, agent: str) -> subprocess.CompletedProcess:
        """Run one real wake firing for *agent* against this root."""
        env = build_scrubbed_env(
            home_dir=self.tmp_path / "home",
            # The stub first, then this interpreter's own directory: the
            # wake refuses a python3 older than the one running this test.
            extra_path_dirs=[self.bin_dir, pathlib.Path(sys.executable).parent],
            extra_env={
                "KANBAN_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.tmp_path / "scratch"),
                "PAUSE_BETWEEN_TASKS": "0",
            },
        )
        (self.tmp_path / "home").mkdir(exist_ok=True)
        cwd = self.tmp_path / "foreign_cwd"
        cwd.mkdir(exist_ok=True)
        return subprocess.run(
            ["bash", str(self.root / "scripts" / "wake" / "claude.sh"), f"--agent={agent}"],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=_WAKE_TIMEOUT_S,
        )


def _output(result: subprocess.CompletedProcess) -> str:
    return f"exit={result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _hold_lines(result: subprocess.CompletedProcess) -> list[str]:
    """The guard's own narration lines from the wake output."""
    return [
        line
        for line in (result.stdout + result.stderr).splitlines()
        if "BLOCKED task(s) need human attention" in line
    ]


# ---------------------------------------------------------------------------
# A task-local block does not hold dispatch
# ---------------------------------------------------------------------------


def test_one_task_local_block_does_not_stop_unrelated_coder_task(tmp_path):
    box = _Sandbox(tmp_path)
    box.add_human_gated_block(_BLOCKED_A, "per_task_timeout", minutes_ago=5)
    box.add_task(_UNRELATED, state="BACKLOG")

    result = box.wake("coder")

    assert result.returncode == 0, _output(result)
    assert call_count(box.control_dir) == 1, _output(result)
    assert box.state(_UNRELATED) == "DONE", _output(result)
    assert box.state(_BLOCKED_A) == "BLOCKED"
    lines = _hold_lines(result)
    assert lines and all(_TASK_LOCAL in line for line in lines), _output(result)
    assert _HELD not in result.stdout + result.stderr


# ---------------------------------------------------------------------------
# A block storm holds dispatch inside the window, not outside it
# ---------------------------------------------------------------------------


def test_two_same_code_blocks_inside_storm_window_hold_dispatch(tmp_path):
    box = _Sandbox(tmp_path)
    # Two roles' tasks: the hold is project-wide, across every agent queue.
    box.add_human_gated_block(_BLOCKED_A, "per_task_timeout", minutes_ago=20)
    box.add_human_gated_block(_BLOCKED_B, "per_task_timeout", minutes_ago=10)
    box.add_task(_UNRELATED, state="BACKLOG")

    result = box.wake("coder")

    assert result.returncode == 0, _output(result)
    assert call_count(box.control_dir) == 0, _output(result)
    assert box.state(_UNRELATED) == "BACKLOG"
    lines = [line for line in _hold_lines(result) if _HELD in line]
    assert len(lines) == 1, _output(result)
    # The narration names the count, the reason_code and why it is systemic.
    assert "2 BLOCKED task(s) need human attention" in lines[0]
    assert "systemic reason_code=per_task_timeout" in lines[0]
    assert f"within block_storm_window={_WINDOW_S}s" in lines[0]
    # It names when the hold lifts: the older block leaves the window 40
    # minutes from now, inside the default one-hour cap.
    assert "the hold lifts at " in lines[0]
    assert "not dispatching new tasks until 20" in lines[0], lines[0]
    assert f"error:   {_HOLD_ERROR_KEY}" in result.stderr, _output(result)
    remedy = next(line for line in result.stderr.splitlines() if line.startswith("remedy:"))
    assert str(box.tasks / _BLOCKED_A / "status.md") in remedy


def test_same_two_blocks_outside_storm_window_do_not_hold_dispatch(tmp_path):
    box = _Sandbox(tmp_path)
    box.add_human_gated_block(_BLOCKED_A, "per_task_timeout", minutes_ago=2 * _WINDOW_S // 60 + 10)
    box.add_human_gated_block(_BLOCKED_B, "per_task_timeout", minutes_ago=10)
    box.add_task(_UNRELATED, state="BACKLOG")

    result = box.wake("coder")

    assert result.returncode == 0, _output(result)
    assert call_count(box.control_dir) == 1, _output(result)
    assert box.state(_UNRELATED) == "DONE"
    lines = _hold_lines(result)
    assert lines and all(_TASK_LOCAL in line for line in lines), _output(result)
    assert _HOLD_ERROR_KEY not in result.stderr


# ---------------------------------------------------------------------------
# A systemic reason code holds dispatch on its own
# ---------------------------------------------------------------------------


def test_block_with_systemic_reason_code_holds_dispatch_on_its_own(tmp_path):
    box = _Sandbox(tmp_path)
    box.add_human_gated_block(_BLOCKED_A, "plugin_load_failed", minutes_ago=5)
    box.add_task(_UNRELATED, state="BACKLOG")

    result = box.wake("coder")

    assert result.returncode == 0, _output(result)
    assert call_count(box.control_dir) == 0, _output(result)
    assert box.state(_UNRELATED) == "BACKLOG"
    lines = [line for line in _hold_lines(result) if _HELD in line]
    assert len(lines) == 1, _output(result)
    assert "1 BLOCKED task(s) need human attention" in lines[0]
    assert "systemic reason_code=plugin_load_failed" in lines[0]
    assert "is on the systemic list" in lines[0]


# ---------------------------------------------------------------------------
# OVERWATCH is never held
# ---------------------------------------------------------------------------


def test_overwatch_is_never_held(tmp_path):
    box = _Sandbox(tmp_path)
    box.add_human_gated_block(_BLOCKED_A, "plugin_load_failed", minutes_ago=5)
    box.add_human_gated_block(_BLOCKED_B, "per_task_timeout", minutes_ago=5)

    result = box.wake("overwatch")

    assert result.returncode == 0, _output(result)
    output = result.stdout + result.stderr
    lines = _hold_lines(result)
    assert lines and all(_OVERWATCH_NOT_HELD in line for line in lines), _output(result)
    assert _HELD not in output
    assert _HOLD_ERROR_KEY not in output
    # The dispatch loop ran: it asked the overwatch queue for work.
    assert "no more BACKLOG tasks in overwatch queue" in output, _output(result)


# ---------------------------------------------------------------------------
# A held wake still runs the WAITING and BLOCKED re-checks
# ---------------------------------------------------------------------------


def test_held_wake_still_runs_waiting_and_blocked_rechecks(tmp_path):
    box = _Sandbox(tmp_path)
    box.add_human_gated_block(_BLOCKED_A, "plugin_load_failed", minutes_ago=5)
    box.add_task(_DONE_PREREQ, state="DONE")
    box.add_task(_WAITING, state="WAITING", prerequisites=f"- {_DONE_PREREQ}")
    # A transient-error block below its retry cap, blocked long enough ago
    # that its backoff window has passed: recheck_blocked_tasks promotes it.
    transient = box.add_task(
        _TRANSIENT,
        state="BLOCKED",
        needs_human="no",
        extra_status=(
            "\n## Transient Retry\nreason_code: transient_api_error\nretry_count: 1\n"
            "retry_limit: 3\nprovider: claude\nlast_error: overloaded\n"
        ),
    )
    write_block_record(
        transient, "transient_api_error", "CODER", ["worktree=gone"], now=_LONG_AGO
    )

    result = box.wake("coder")

    assert result.returncode == 0, _output(result)
    output = result.stdout + result.stderr
    assert any(_HELD in line for line in _hold_lines(result)), _output(result)
    # Held: nothing was dispatched, so both promoted tasks are left BACKLOG.
    assert call_count(box.control_dir) == 0, _output(result)
    assert box.state(_WAITING) == "BACKLOG", _output(result)
    assert box.state(_TRANSIENT) == "BACKLOG", _output(result)
    assert box.state(_BLOCKED_A) == "BLOCKED"
    assert "re-checking WAITING tasks" in output
    assert "re-checking BLOCKED tasks" in output
    queue = (box.queues / "coder_backlog.md").read_text(encoding="utf-8")
    assert f"- [ ] {_WAITING}" in queue
    assert f"- [ ] {_TRANSIENT}" in queue
