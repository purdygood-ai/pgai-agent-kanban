"""
test_idle_pass_lock_scope.py
============================
run_project_chain (team/scripts/lib/wake_driver.sh) holds a project's repo
lock on an idle pass -- no dispatchable task, and no discovery scan to come
-- only for the steps that need the repository. The WAITING/BLOCKED
re-checks and the intake census scan after the release, and each write they
make retakes the lock for just that write (wake_driver_idle.sh):

  1. An idle CODER pass with a fake census that sleeps holds the repo lock
     under 2 s, and under half the smallest stagger the large wake tier
     installs (read from templates/install/crontab-large.example). The
     census runs twice, both times without the lock, and the re-check and
     census lines are the ones a locked pass logs. The measured hold is
     printed and recorded as the test property ``idle_lock_hold_seconds``.
  2. A dispatchable task still runs under the lock: the census before its
     dispatch runs locked, and the task is dispatched and finishes.
  3. A re-check that makes a task dispatchable sends the pass back for the
     lock, and the promoted task is dispatched under it.
  4. Each idle-pass write -- a WAITING promotion, a BLOCKED promotion, an
     intake-item closure -- is made while this chain holds the repo lock,
     whose provenance names the promoted task (none for the closure), and
     each such hold stays under the same 2 s limit (the longest recorded as
     ``idle_write_lock_hold_seconds``).
  5. A write that finds the lock held by another process is not made: the
     task stays WAITING with its [W] marker, the pass ends cleanly, and the
     next firing makes the write.

Each test runs one real wake firing (`bash scripts/wake/claude.sh
--agent=coder`) against a throwaway kanban root under tmp_path: a copy of
this tree's scripts/, pgai_agent_kanban/, workflows/, pm-agent/ and config/,
one document-workflow project (no dev tree, worktree or repository), and a
PATH-shadowing `claude` stub that marks a dispatched task DONE. The copied
wake_lifecycle.sh gets a census wrapper that records whether the chain
holds the lock, sleeps, then runs the real census, and recorders around the
three writers that note the lock state at the moment of each write. When a
test asks for it, the first WAITING re-check starts a separate process that
takes the repo lock (as an operator tool would) and keeps it until the test
ends it. The environment is built from an explicit set with every
live-install name pointed into the sandbox.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys


from tests.fixtures.claude_provider_stub import install_claude_provider_stub
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_MIRRORED_DIRS = ("scripts", "pgai_agent_kanban", "workflows", "pm-agent", "config")
_PROJECT = "idle-probe"
_AGENTS = ("pm", "coder", "writer", "tester", "cm", "overwatch")
_WAKE_TIMEOUT_S = 240
# Long enough that a census run under the lock would push the hold past
# every bound below on its own.
_CENSUS_SLEEP_S = 2.5
_IDLE_HOLD_LIMIT_S = 2.0
_HELD_RE = re.compile(rf"project {re.escape(_PROJECT)}: repo lock released \(held (\d+\.\d+)s\)")
_WRITE_HELD_RE = re.compile(
    rf"project {re.escape(_PROJECT)}: idle pass: (.+?) made under the retaken repo lock \(held (\d+\.\d+)s, exit 0\)"
)
_REQUIREMENT = "idle-probe-report-20260101"

_CENSUS_WRAPPER = r"""

# --- test wrappers: record the lock state at the census and at each write ---
_t_lock_state() {
  local _t_lock
  _t_lock="$(pp_repo_lock_path "${_CURRENT_PROJECT:-}")"
  if [[ -f "$_t_lock" ]] && grep -qx "pid=$$" "$_t_lock"; then
    echo "HELD:$(sed -n 's/^task=//p' "$_t_lock")"
  else
    echo FREE
  fi
}
_t_rename() {
  eval "$(declare -f "$1" | sed "1s/^$1/_t_real_$1/")"
}

_t_rename sweep_running_intake_census
sweep_running_intake_census() {
  local _t_state
  _t_state="$(_t_lock_state)"
  echo "${_t_state%%:*}" >> "$T_CENSUS_REC"
  sleep "$T_CENSUS_SLEEP"
  _t_real_sweep_running_intake_census "$@"
}

_t_rename promote_waiting_to_backlog
promote_waiting_to_backlog() {
  echo "waiting $1 $(_t_lock_state)" >> "$T_WRITE_REC"
  _t_real_promote_waiting_to_backlog "$@"
}

_t_rename promote_blocked_to_backlog
promote_blocked_to_backlog() {
  echo "blocked $1 $(_t_lock_state)" >> "$T_WRITE_REC"
  _t_real_promote_blocked_to_backlog "$@"
}

_t_rename _intake_close_item
_intake_close_item() {
  echo "census $3 $(_t_lock_state)" >> "$T_WRITE_REC"
  _t_real__intake_close_item "$@"
}

# With T_HOLD_SCRIPT set, the first WAITING re-check (just after the idle
# pass released the lock) starts a detached process that takes the lock.
_t_rename recheck_waiting_tasks
recheck_waiting_tasks() {
  if [[ -n "${T_HOLD_SCRIPT:-}" && ! -e "$T_HOLD_READY" ]]; then
    ( bash "$T_HOLD_SCRIPT" "$T_HOLD_LIB" "$(pp_repo_lock_path "${_CURRENT_PROJECT:-}")" "$T_HOLD_READY" \
        </dev/null >/dev/null 2>&1 & )
    local _t_i
    for _t_i in $(seq 1 100); do
      [[ -s "$T_HOLD_READY" ]] && break
      sleep 0.1
    done
  fi
  _t_real_recheck_waiting_tasks "$@"
}
"""

# A process other than the wake that takes the repo lock and keeps it until
# killed: inherited descriptors are closed first, so the wake's own locks
# are not carried along, and exec keeps the pid the provenance records.
_HOLDER_SCRIPT = r"""#!/usr/bin/env bash
for _fd in /proc/$$/fd/*; do
  _n="${_fd##*/}"
  [[ "$_n" =~ ^[0-9]+$ ]] && (( _n > 2 && _n != 255 )) && eval "exec ${_n}>&-" 2>/dev/null
done
source "$1/lock_provenance.sh"
pgai_lock_acquire "$2" operator "$$" "operator-hold" none _holder_fd || exit 1
printf '%s\n' "$$" > "$3.tmp" && mv "$3.tmp" "$3"
exec sleep 120
"""


def _smallest_large_tier_stagger() -> int:
    text = (_TEAM_DIR / "templates" / "install" / "crontab-large.example").read_text(encoding="utf-8")
    sleeps = sorted(
        {
            int(m.group(1))
            for line in text.splitlines()
            if not line.lstrip().startswith("#")
            for m in re.finditer(r"--sleep=(\d+)", line)
        }
    )
    gaps = [b - a for a, b in zip(sleeps, sleeps[1:]) if b > a]
    assert gaps, f"no stagger found in crontab-large.example: {sleeps}"
    return min(gaps)


class _Sandbox:
    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.tmp_path = tmp_path
        self.root = tmp_path / "kanban"
        self.project = self.root / "projects" / _PROJECT
        self.tasks = self.project / "tasks"
        self.queues = self.tasks / "queues"
        self.control_dir = tmp_path / "stub-control"
        self.bin_dir = tmp_path / "stub-bin"
        self.census_rec = tmp_path / "census.rec"
        self.write_rec = tmp_path / "write.rec"
        self.hold_ready = tmp_path / "holder.pid"
        self._build()

    def _build(self) -> None:
        scratch = self.tmp_path / "scratch"
        scratch.mkdir(parents=True)
        self.queues.mkdir(parents=True)
        (self.root / "logs").mkdir(parents=True)
        (self.root / "locks").mkdir(parents=True)
        (self.project / "requirements").mkdir(parents=True)
        (self.root / "kanban.cfg").write_text(
            "[paths]\n"
            f"tmp_root = {scratch}\n"
            "tmp_subdir = pgai_kanban_tmp\n\n"
            "[chain]\npm_mode = manual\n\n"
            "[wake]\nmax_tasks_per_wake = 3\n",
            encoding="utf-8",
        )
        (self.root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority=1\nenabled=true\n", encoding="utf-8")
        (self.project / "project.cfg").write_text(
            f"[project]\nproject_name = {_PROJECT}\nworkflow_type = document\n", encoding="utf-8"
        )
        for agent in _AGENTS:
            (self.queues / f"{agent}_backlog.md").write_text(f"# {agent} backlog\n\n", encoding="utf-8")
        for name in _MIRRORED_DIRS:
            shutil.copytree(_TEAM_DIR / name, self.root / name, ignore=shutil.ignore_patterns("__pycache__"))
        lifecycle = self.root / "scripts" / "lib" / "wake_lifecycle.sh"
        lifecycle.write_text(lifecycle.read_text(encoding="utf-8") + _CENSUS_WRAPPER, encoding="utf-8")
        install_claude_provider_stub(self.bin_dir, self.control_dir)

    def add_task(self, task_id: str, *, state: str, prerequisites: str = "none", status_extra: str = "") -> None:
        role = task_id.split("-", 1)[0]
        task_dir = self.tasks / task_id
        task_dir.mkdir(parents=True)
        (task_dir / "README.md").write_text(
            f"# Task: {task_id}\n\n## Task ID\n{task_id}\n\n## Role\n{role}\n\n"
            "## Workflow Type\ndocument\n\n## Git Repo\nnone\n\n## Working Directory\nnone\n\n"
            f"## Source Branch\nnone\n\n## Prerequisites\n{prerequisites}\n",
            encoding="utf-8",
        )
        (task_dir / "status.md").write_text(
            f"# Status\n\n## Task\n{task_id}\n\n## State\n{state}\n\n## Summary\nnone\n\n"
            "## Blockers\nnone\n\n## Needs Human\nno\n" + status_extra,
            encoding="utf-8",
        )
        marker = {"BLOCKED": "B", "WAITING": "W", "DONE": "x"}.get(state, " ")
        with (self.queues / f"{role.lower()}_backlog.md").open("a", encoding="utf-8") as handle:
            handle.write(f"- [{marker}] {task_id} — fixture task\n")

    def state(self, task_id: str) -> str:
        text = (self.tasks / task_id / "status.md").read_text(encoding="utf-8")
        return text.split("## State", 1)[1].strip().splitlines()[0].strip()

    def add_running_report_requirement(self) -> pathlib.Path:
        """A running testing-only intake item with no task citing it: the census closes it."""
        path = self.project / "requirements" / f"{_REQUIREMENT}.md"
        path.write_text(
            f"# Requirements: {_REQUIREMENT}\n\n## Status\nrunning\n\n## Workflow Type\ntesting-only\n",
            encoding="utf-8",
        )
        return path

    def wake(
        self, agent: str = "coder", *, hold_lock: bool = False, census_sleep: float = _CENSUS_SLEEP_S
    ) -> subprocess.CompletedProcess:
        (self.tmp_path / "home").mkdir(exist_ok=True)
        extra_env = {
            "KANBAN_ROOT": str(self.root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.tmp_path / "scratch"),
            "PAUSE_BETWEEN_TASKS": "0",
            "T_CENSUS_REC": str(self.census_rec),
            "T_CENSUS_SLEEP": str(census_sleep),
            "T_WRITE_REC": str(self.write_rec),
        }
        if hold_lock:
            holder = self.tmp_path / "holder.sh"
            holder.write_text(_HOLDER_SCRIPT, encoding="utf-8")
            extra_env.update(
                {
                    "T_HOLD_SCRIPT": str(holder),
                    "T_HOLD_LIB": str(self.root / "scripts" / "lib"),
                    "T_HOLD_READY": str(self.hold_ready),
                }
            )
        env = build_scrubbed_env(
            home_dir=self.tmp_path / "home",
            extra_path_dirs=[self.bin_dir, pathlib.Path(sys.executable).parent],
            extra_env=extra_env,
        )
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

    def census_states(self) -> list[str]:
        return self.census_rec.read_text(encoding="utf-8").split() if self.census_rec.exists() else []

    def writes(self) -> list[list[str]]:
        """One [writer, subject, lock state] per recorded write, in order."""
        if not self.write_rec.exists():
            return []
        return [line.split() for line in self.write_rec.read_text(encoding="utf-8").splitlines() if line.strip()]

    def release_holder(self) -> None:
        """End the contending lock holder, if one was started."""
        if not self.hold_ready.exists():
            return
        try:
            os.kill(int(self.hold_ready.read_text(encoding="utf-8").strip()), signal.SIGTERM)
        except (ProcessLookupError, ValueError):
            pass


def _output(result: subprocess.CompletedProcess) -> str:
    return result.stdout + result.stderr


def _holds(result: subprocess.CompletedProcess) -> list[float]:
    return [float(m.group(1)) for m in _HELD_RE.finditer(_output(result))]


def test_an_idle_pass_releases_the_lock_before_the_census_and_rechecks(tmp_path, record_property) -> None:
    box = _Sandbox(tmp_path)

    result = box.wake("coder")
    out = _output(result)

    assert result.returncode == 0, out
    holds = _holds(result)
    assert len(holds) == 1, f"expected one repo-lock release line:\n{out}"
    hold = holds[0]
    print(f"idle pass repo-lock hold: {hold:.2f}s (census sleeps {_CENSUS_SLEEP_S}s per run)")
    record_property("idle_lock_hold_seconds", hold)
    stagger = _smallest_large_tier_stagger()
    assert hold < _IDLE_HOLD_LIMIT_S, f"idle pass held the repo lock {hold:.2f}s"
    assert hold < stagger / 2, f"idle hold {hold:.2f}s is not under half the {stagger}s large-tier stagger"

    assert box.census_states() == ["FREE", "FREE"], "the census ran under the repo lock"
    assert out.count("re-checking WAITING tasks for satisfied prerequisites...") == 2, out
    assert out.count("re-checking BLOCKED tasks for cleared blockers...") == 2, out
    assert out.count(f"sweep-census: project {_PROJECT}:") == 2, out
    assert "no pending Claude task found in coder queue" in out
    assert re.search(
        rf"project {re.escape(_PROJECT)}: done: completed=0 .*reason=\"no more BACKLOG tasks in coder queue\"", out
    ), out
    assert out.index("repo lock released") < out.index("re-checking WAITING tasks"), out


def test_a_dispatchable_task_still_runs_under_the_lock(tmp_path) -> None:
    box = _Sandbox(tmp_path)
    task = "CODER-20260101-001-ready-work"
    box.add_task(task, state="BACKLOG")

    result = box.wake("coder")
    out = _output(result)

    assert result.returncode == 0, out
    assert box.state(task) == "DONE", out
    states = box.census_states()
    assert states and states[0] == "HELD", f"the census before the dispatch ran without the lock: {states}"
    assert "idle pass" not in out


def test_a_task_a_recheck_makes_dispatchable_runs_under_the_retaken_lock(tmp_path) -> None:
    box = _Sandbox(tmp_path)
    prereq = "CODER-20260101-002-finished-prerequisite"
    waiting = "CODER-20260101-003-waiting-on-finished"
    box.add_task(prereq, state="DONE")
    box.add_task(waiting, state="WAITING", prerequisites=prereq)

    result = box.wake("coder")
    out = _output(result)

    assert result.returncode == 0, out
    assert "a re-check made a coder task dispatchable; retaking the repo lock" in out, out
    assert box.state(waiting) == "DONE", out
    states = box.census_states()
    assert states[0] == "FREE" and "HELD" in states[1:], states


def test_each_idle_pass_queue_write_holds_the_repo_lock(tmp_path, record_property) -> None:
    box = _Sandbox(tmp_path)
    prereq = "CODER-20260101-004-finished-prerequisite"
    waiting = "CODER-20260101-005-waiting-on-finished"
    blocked = "CODER-20260101-006-blocked-on-closed-rc"
    box.add_task(prereq, state="DONE")
    box.add_task(waiting, state="WAITING", prerequisites=prereq)
    box.add_task(blocked, state="BLOCKED", status_extra="\n## Blocked Reason\nwaiting for the Active RC to close\n")
    (box.project / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n",
        encoding="utf-8",
    )
    requirement = box.add_running_report_requirement()

    result = box.wake("coder", census_sleep=0)
    out = _output(result)

    assert result.returncode == 0, out
    assert "the intake census scan without the repo lock and retake it for each write" in out, out
    writes = box.writes()
    assert [w[:2] for w in writes] == [["waiting", waiting], ["blocked", blocked], ["census", _REQUIREMENT]], writes
    assert writes[0][2] == f"HELD:{waiting}", f"the WAITING promotion was not made under the repo lock: {writes}"
    assert writes[1][2] == f"HELD:{blocked}", f"the BLOCKED promotion was not made under the repo lock: {writes}"
    assert writes[2][2] == "HELD:none", f"the intake closure was not made under the repo lock: {writes}"
    assert box.census_states()[0] == "FREE", "the idle pass's census scan ran under the repo lock"

    write_holds = {m.group(1): float(m.group(2)) for m in _WRITE_HELD_RE.finditer(out)}
    assert set(write_holds) == {
        f"WAITING promotion of {waiting}",
        f"BLOCKED promotion of {blocked}",
        f"intake item '{_REQUIREMENT}' closure",
    }, out
    longest = max(write_holds.values())
    print(f"idle pass write lock holds: {write_holds}")
    record_property("idle_write_lock_hold_seconds", longest)
    assert longest < _IDLE_HOLD_LIMIT_S, f"an idle-pass write held the repo lock {longest:.2f}s"

    assert "## Status\ndone" in requirement.read_text(encoding="utf-8"), out
    assert box.state(waiting) == "DONE", out
    assert box.state(blocked) == "DONE", out


def test_an_idle_pass_write_that_cannot_take_the_lock_is_left_for_the_next_firing(tmp_path) -> None:
    box = _Sandbox(tmp_path)
    prereq = "CODER-20260101-007-finished-prerequisite"
    waiting = "CODER-20260101-008-waiting-on-finished"
    box.add_task(prereq, state="DONE")
    box.add_task(waiting, state="WAITING", prerequisites=prereq)
    queue = box.queues / "coder_backlog.md"

    try:
        result = box.wake("coder", hold_lock=True, census_sleep=0)
    finally:
        box.release_holder()
    out = _output(result)

    assert result.returncode == 0, out
    assert box.hold_ready.exists(), f"the contending holder never took the lock:\n{out}"
    assert f"idle pass: WAITING promotion of {waiting} not written -- repo lock held by" in out, out
    assert "operator-hold" in out, out
    assert f"idle pass: WAITING promotion of {waiting} not written -- an earlier write this pass" in out, out
    assert box.writes() == [], f"a write ran without the repo lock: {box.writes()}"
    assert box.state(waiting) == "WAITING", out
    assert f"- [W] {waiting}" in queue.read_text(encoding="utf-8")
    assert "retaking the repo lock to dispatch" not in out, out
    assert "repo lock held elsewhere when an idle-pass write retook it" in out, out

    retry = box.wake("coder", census_sleep=0)
    retry_out = _output(retry)

    assert retry.returncode == 0, retry_out
    assert box.writes() == [["waiting", waiting, f"HELD:{waiting}"]], box.writes()
    assert box.state(waiting) == "DONE", retry_out
