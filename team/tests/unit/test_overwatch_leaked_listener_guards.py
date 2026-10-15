"""
test_overwatch_leaked_listener_guards.py
========================================
check-leaked-listeners.sh stops only listeners that are really leaked:

  1. A listener inside a WORKING task's worktree is not signalled, whatever
     its age.
  2. A listener younger than [overwatch] leaked_listener_min_age_seconds
     (default 3600) is not signalled.
  3. An old listener under a finished task's worktree is signalled and
     logged under the project whose temp area holds it -- never under
     another project swept in the same firing.
  4. overwatch-sweep.sh scans /proc once per sweep, not once per project.

Each fixture listener is a short-lived sleeping process bound to an
ephemeral loopback port, started in its own session (so it is not in the
check's process group) with its cwd inside a throwaway temp root, and
killed in teardown. The kanban root, temp root and every log live under
tmp_path.
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_CHECK = "scripts/lib/overwatch-checks/check-leaked-listeners.sh"
_PROTOCOL = "scripts/lib/overwatch_protocol.sh"
_SWEEP = _TEAM_DIR / "scripts" / "overwatch-sweep.sh"

_LISTENER = textwrap.dedent("""\
    import pathlib, socket, sys, time
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    pathlib.Path(sys.argv[1]).write_text("ready")
    time.sleep(60)
""")

_LIVE = "CODER-20261012-001-live"
_DONE = "CODER-20261012-002-done"


class Sandbox:
    """A kanban root with projects a and b, and a temp root beside it."""

    def __init__(self, tmp_path: pathlib.Path, min_age: "str | None") -> None:
        self.tmp_path = tmp_path
        self.root = tmp_path / "kanban"
        self.temp = tmp_path / "tmp"
        self.temp.mkdir()
        (self.root / "projects").mkdir(parents=True)
        (self.root / "projects.cfg").write_text(
            "[project:a]\npriority = 1\n\n[project:b]\npriority = 2\n", encoding="utf-8"
        )
        cfg = "[paths]\n"
        if min_age is not None:
            cfg += f"\n[overwatch]\nleaked_listener_min_age_seconds = {min_age}\n"
        (self.root / "kanban.cfg").write_text(cfg, encoding="utf-8")
        for name in ("a", "b"):
            proj = self.root / "projects" / name
            (proj / "overwatch" / "backups").mkdir(parents=True)
            (proj / "project.cfg").write_text(f"[project]\nproject_name = {name}\n", encoding="utf-8")
            for task, state in ((_LIVE, "WORKING"), (_DONE, "DONE")):
                (proj / "tasks" / task).mkdir(parents=True)
                (proj / "tasks" / task / "status.md").write_text(f"# Status\n\n## State\n{state}\n", encoding="utf-8")
        self.procs: list[subprocess.Popen] = []

    def listener(self, project: str, task: str) -> subprocess.Popen:
        cwd = self.temp / "projects" / project / "worktrees" / task
        cwd.mkdir(parents=True, exist_ok=True)
        ready = self.tmp_path / f"ready-{project}-{task}-{len(self.procs)}"
        proc = subprocess.Popen(
            [sys.executable, "-c", _LISTENER, str(ready)], cwd=str(cwd), start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.procs.append(proc)
        deadline = time.monotonic() + 10
        while not ready.exists():
            assert time.monotonic() < deadline, "fixture listener did not start"
            time.sleep(0.05)
        return proc

    def env(self) -> dict:
        return {
            "KANBAN_ROOT": str(self.root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.temp),
        }

    def check(self, project: str):
        script = textwrap.dedent(f"""\
            set -uo pipefail
            unset PGAI_TASKS_DIR PGAI_PROJECT_ROOT PGAI_PROJECT_NAME OVERWATCH_LEAKED_LISTENER_SCAN
            source {_PROTOCOL}
            export OVERWATCH_PROJECT={project}
            source {_CHECK}
            overwatch_check_leaked_listeners
            echo "RC=$?"
        """)
        return run_bash(self.tmp_path, script, extra_env=self.env(), timeout=60)

    def actions(self, project: str) -> str:
        log = self.root / "projects" / project / "overwatch" / "actions.log"
        return log.read_text(encoding="utf-8") if log.exists() else ""

    def close(self) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)


def _ended(proc: subprocess.Popen) -> bool:
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        return False
    return True


@pytest.fixture
def sandbox_factory(tmp_path: pathlib.Path):
    made: list[Sandbox] = []

    def make(min_age: "str | None") -> Sandbox:
        box = Sandbox(tmp_path, min_age)
        made.append(box)
        return box

    yield make
    for box in made:
        box.close()


def test_listener_of_a_working_task_is_not_signalled(sandbox_factory) -> None:
    box = sandbox_factory("0")
    proc = box.listener("a", _LIVE)

    result = box.check("a")

    assert "RC=0" in result.stdout, result.stderr
    assert proc.poll() is None, "a WORKING task's listener was signalled"
    assert f"belongs to WORKING task {_LIVE}" in result.stderr, result.stderr


def test_listener_younger_than_the_threshold_is_not_signalled(sandbox_factory) -> None:
    box = sandbox_factory(None)
    proc = box.listener("a", _DONE)

    result = box.check("a")

    assert "RC=0" in result.stdout, result.stderr
    assert proc.poll() is None, "a young listener was signalled"
    assert "under the 3600s threshold" in result.stderr, result.stderr


def test_old_listener_of_a_finished_task_is_signalled_under_its_own_project(sandbox_factory) -> None:
    box = sandbox_factory("0")
    proc = box.listener("b", _DONE)

    other = box.check("a")
    assert "RC=0" in other.stdout, other.stderr
    assert proc.poll() is None, "project a's check signalled project b's listener"
    assert "killed-leaked-listener" not in box.actions("a")

    own = box.check("b")

    assert "RC=0" in own.stdout, own.stderr
    assert _ended(proc), own.stderr
    assert f"pid:{proc.pid}" in box.actions("b") and "killed-leaked-listener" in box.actions("b")
    assert "killed-leaked-listener" not in box.actions("a")


def test_sweep_scans_for_listeners_once_for_all_projects(sandbox_factory, tmp_path: pathlib.Path) -> None:
    box = sandbox_factory("0")
    proc = box.listener("b", _DONE)
    env = dict(os.environ)
    for name in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "PGAI_PROJECT_NAME", "OVERWATCH_LEAKED_LISTENER_SCAN"):
        env.pop(name, None)
    env.update(box.env())

    result = subprocess.run(
        ["bash", str(_SWEEP), "--only", "leaked-listeners"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=120,
    )

    assert result.returncode == 0, result.stderr
    scans = [line for line in result.stderr.splitlines() if "scanning /proc for listener processes" in line]
    assert len(scans) == 1, result.stderr
    assert "[a] running leaked-listeners" in result.stderr and "[b] running leaked-listeners" in result.stderr
    assert _ended(proc), result.stderr
    assert "killed-leaked-listener" in box.actions("b")
    assert "killed-leaked-listener" not in box.actions("a")
    assert not list(box.temp.glob(".leaked-listener-scan.*")), "the sweep's scan file was left behind"
