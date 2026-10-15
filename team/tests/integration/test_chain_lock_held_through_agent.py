"""
test_chain_lock_held_through_agent.py
======================================
End-to-end coverage for the per-project repo lock's hold discipline: a wake
chain that took its project's repo lock keeps it, on one open fd, through
its whole dispatched subagent invocation and its finalize (the post-agent
merge check and the queue-marker write), a sibling chain that wakes beside
it defers on flock contention, and -- the property this fixture exists to
pin -- a chain that has genuinely LOST the lock abandons its own fd on the
way out without ever touching the file the new holder now owns.

Why the third point is the load-bearing one. The lock itself was always
held across the subagent: flock is on an open file description, and the
subagent's processes merely inherit the fd. What could free it mid-run was
the exit of a DIFFERENT chain, one that had earlier lost the same lock:
its merge-boundary stop (`return 3` from process_one_task, wake_driver.sh)
used to run with errexit re-enabled, so the nonzero return terminated the
whole wake process before run_project_chain's end-of-chain cleanup could
abandon the fd; the EXIT sweep then found the lock still in its
bookkeeping and released it -- truncating and unlinking the path without
checking that the record on disk was still its own. The path by then
belonged to the live holder, so the next sibling's `flock -n` succeeded on
a fresh inode while that holder's subagent was still running, and that
holder lost the lock in turn at its own next boundary. One seeded loss
therefore propagated indefinitely, every hop discarding a finished
subagent's work.

Scenario, driven through the REAL production entry point (a real
`bash scripts/wake/claude.sh --agent=writer` subprocess per chain, with a
PATH-shadowing `claude` stub -- tests/fixtures/claude_provider_stub.py --
so no real CLI, network, or token is ever reached):

  1. Chain L starts against a project whose writer queue holds the lock-holder task (L1) and
     the waiting task (W1). It takes the repo lock and dispatches the lock-holder task (L1) through a stub
     that deliberately runs for several seconds.
  2. While L's subagent runs, this fixture seeds a loss exactly as an
     external reap would (team/scripts/release-locks.sh --force, or an
     over-aggressive age threshold): it unlinks L's lock file, then starts
     chain W -- a separate wake subprocess against the SAME project and
     queue. W acquires the now-free path for real and dispatches the waiting task (W1)
     through a stub that runs for longer still.
  3. L's subagent finishes. L's post-agent merge-boundary reverification
     finds W's record on disk and stops cleanly before finalize. L must
     then ABANDON its fd: exit 0, narrate the abandon, and leave W's lock
     file -- inode, flock and provenance -- exactly as W wrote it.
  4. With L gone and W's subagent still running, chain S starts. It must
     find the lock held by W and log the healthy deferring path
     ("repo lock held by holder=writer pid=<W> ... deferring"), never
     "starting" a chain of its own.
  5. W's subagent finishes; W's finalize completes on this first dispatch:
     the waiting task (W1)'s queue marker becomes [x], its status.md reads DONE, and no
     stale-active reset returns it to BACKLOG.

Acceptance criteria exercised (this task's README):
  - a chain holds the lock through a deliberately long stubbed subagent
    on a scratch project root; a second chain beside it logs the deferring
    path while the first still holds the lock;
  - the first chain's finalize completes on the first dispatch: [A]
    becomes [x] and no stale-active reset re-dispatches the task;
  - a chain that lost the lock leaves the live holder's file untouched on
    every exit path (the direct regression for the cascade above).

Design notes
------------
- Every kanban root built here is a full, self-contained copy of this dev
  tree's scripts/, pgai_agent_kanban/, workflows/, pm-agent/, and config/
  directories, mirroring test_chain_lock_reverification.py's own builder.
- The project uses the document workflow (git_mode=none): no dev tree or
  git repository is needed, since the hold discipline under test is a
  property of the wake chain's own lock handling, not of git.
- Each chain gets its OWN stub bin directory wrapping the shared provider
  stub in a fixed per-chain delay, so the overlap between L's and W's
  subagent runs is controlled by the fixture rather than by luck.
- All temp paths use pytest's tmp_path. No bare /tmp.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import stat
import subprocess
import threading
import time

from tests.fixtures.claude_provider_stub import install_claude_provider_stub
from tests.fixtures.scrubbed_subprocess_env import scrub_wake_tunable_env

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_WAKE_CLAUDE_REL = pathlib.Path("scripts") / "wake" / "claude.sh"
_AGENT = "writer"
_PROJECT = "shared-project"
_TASK_L1 = "WRITER-20260101-001-lock-holder-long"
_TASK_W1 = "WRITER-20260101-002-lock-waiter"

# How long each chain's stubbed subagent runs. L must finish -- and hit its
# merge-boundary stop -- while W's subagent is still running, and S must
# get its turn in that same window; the gap between the two is the
# fixture's whole timing margin, so it is generous.
_L_AGENT_SECONDS = 8
_W_AGENT_SECONDS = 24
_SUBPROCESS_TIMEOUT_SECONDS = 150

# Host env vars this suite's own live-wake context may have exported --
# stripped from every subprocess env built below (docs/test-authoring-
# guidelines.md, Anti-pattern 2), mirroring test_chain_lock_
# reverification.py's own _LEAK_PREFIXES.
_LEAK_PREFIXES = (
    "PGAI_", "PP_", "KANBAN_ROOT", "CLAUDE_", "AI_AGENT", "STOP_FILE",
    "TMPDIR", "MAX_TASK_SECONDS", "MAX_RUNTIME_SECONDS", "MAX_TASKS_PER_WAKE",
    "MAX_PROJECT_SECONDS", "KILL_GRACE_SECONDS", "PAUSE_BETWEEN_TASKS",
    "STOP_ON_BLOCKED", "AI_AUTH_MODE", "BASH_MAX_TIMEOUT_MS", "DASHBOARD_",
    "PM_MAX_TASKS",
)


def _clean_base_env() -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not any(k == p or k.startswith(p) for p in _LEAK_PREFIXES)
    }
    # Every [wake] tunable a wake exports, LOCK_WAIT_SECONDS included, so
    # the sandbox kanban.cfg's lock_wait_seconds = 0 governs both chains.
    return scrub_wake_tunable_env(env)


# ---------------------------------------------------------------------------
# Kanban root / task builders
# ---------------------------------------------------------------------------


def _write_stub_task(tasks_dir: pathlib.Path, task_id: str) -> None:
    """A minimal WRITER/document-workflow task README + BACKLOG status.md --
    document workflow (git_mode=none) means no worktree is created, so a
    task moves BACKLOG -> WORKING -> DONE through nothing but the provider
    stub."""
    task_dir = tasks_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "README.md").write_text(
        f"# Task: {task_id}\n\n"
        f"## Task ID\n{task_id}\n\n"
        "## Role\nWRITER\n\n"
        "## Workflow Type\ndocument\n\n"
        "## Working Directory\nnone\n\n"
        "## Source Branch\nnone\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{task_id}\n\n## State\nBACKLOG\n\n## Blockers\nnone\n",
        encoding="utf-8",
    )


def _build_kanban_root(
    root: pathlib.Path, project_name: str, task_ids: list[str], *, tmp_scratch: pathlib.Path
) -> pathlib.Path:
    """Build a full, self-contained kanban root at *root*, registering one
    document-workflow project (git_mode=none) whose writer backlog queues
    every id in *task_ids*, in order, all BACKLOG."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "kanban.cfg").write_text(
        "[paths]\n"
        f"tmp_root = {tmp_scratch}\n"
        "tmp_subdir = pgai_kanban_tmp\n\n"
        "[chain]\npm_mode = automatic\n\n"
        "[wake]\nmax_tasks_per_wake = 5\n"
        # lock_wait_seconds = 0: the contention under test is a wake chain
        # holding the lock, which a wake otherwise waits up to 120 s for;
        # this fixture pins the immediate-deferral path.
        "lock_wait_seconds = 0\n",
        encoding="utf-8",
    )
    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\npriority=1\nenabled=true\n\n", encoding="utf-8"
    )

    proj = root / "projects" / project_name
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "project.cfg").write_text(
        "[project]\n"
        f"project_name = {project_name}\n"
        "workflow_type = document\n",
        encoding="utf-8",
    )
    queues_dir = proj / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)
    for agent in ("pm", "tester", "cm"):
        (queues_dir / f"{agent}_backlog.md").write_text(
            f"# {agent} backlog\n\nNo tasks queued by this fixture.\n", encoding="utf-8"
        )

    for task_id in task_ids:
        _write_stub_task(proj / "tasks", task_id)
    lines = "\n".join(f"- [ ] {task_id} — stub task" for task_id in task_ids)
    (queues_dir / f"{_AGENT}_backlog.md").write_text(
        f"# {_AGENT} backlog\n\n{lines}\n", encoding="utf-8"
    )

    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)

    # Full, real copies -- the same set install.sh mirrors into a live
    # install -- so the wake script invoked as a subprocess is
    # byte-identical to production.
    shutil.copytree(_TEAM_DIR / "scripts", root / "scripts")
    shutil.copytree(_TEAM_DIR / "pgai_agent_kanban", root / "pgai_agent_kanban")
    shutil.copytree(_TEAM_DIR / "workflows", root / "workflows")
    shutil.copytree(_TEAM_DIR / "pm-agent", root / "pm-agent")
    shutil.copytree(_TEAM_DIR / "config", root / "config")

    return root


def _lock_path(kanban_root: pathlib.Path, project_name: str) -> pathlib.Path:
    """Mirrors pp_repo_lock_path's own resolution -- the exact lock file
    run_project_chain acquires for *project_name*."""
    repo_id = "".join(c for c in project_name.replace("/", "_") if c.isalnum() or c in "_-")
    return kanban_root / "locks" / f"repo-wake-{repo_id}.lock"


def _lock_field(lock_file: pathlib.Path, key: str) -> str:
    """The *key* field of *lock_file*'s provenance record ('' when absent)."""
    try:
        text = lock_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    for line in text.splitlines():
        if line.startswith(f"{key}="):
            return line[len(key) + 1:].strip()
    return ""


def _lock_pid(lock_file: pathlib.Path) -> str:
    """The pid field of *lock_file*'s provenance record ('' when absent)."""
    return _lock_field(lock_file, "pid")


def _task_status_text(kanban_root: pathlib.Path, task_id: str) -> str:
    return (kanban_root / "projects" / _PROJECT / "tasks" / task_id / "status.md").read_text(
        encoding="utf-8"
    )


def _queue_text(kanban_root: pathlib.Path) -> str:
    return (
        kanban_root / "projects" / _PROJECT / "tasks" / "queues" / f"{_AGENT}_backlog.md"
    ).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Per-chain provider stub with a fixed dispatch delay
# ---------------------------------------------------------------------------


def _install_delayed_stub(bin_dir: pathlib.Path, control_dir: pathlib.Path, delay_seconds: int) -> None:
    """Install the shared provider stub under *bin_dir*/real and shadow it
    with a `claude` wrapper that sleeps *delay_seconds* before delegating a
    task dispatch -- the "deliberately long subagent". `--version` queries
    (the wake's identity-log block) pass straight through, undelayed."""
    real_dir = bin_dir / "real"
    install_claude_provider_stub(real_dir, control_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / "claude"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -u\n"
        'for _arg in "$@"; do\n'
        '  if [[ "$_arg" == "--version" ]]; then\n'
        f'    exec "{real_dir / "claude"}" "$@"\n'
        "  fi\n"
        "done\n"
        f"sleep {delay_seconds}\n"
        f'exec "{real_dir / "claude"}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _wake_env(kanban_root: pathlib.Path, stub_bin_dir: pathlib.Path) -> dict:
    env = _clean_base_env()
    env["PATH"] = f"{stub_bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["PGAI_TASKS_DIR"] = str(kanban_root / "tasks")
    env["HOME"] = str(kanban_root.parent)
    env["PAUSE_BETWEEN_TASKS"] = "1"
    return env


# ---------------------------------------------------------------------------
# A wake chain subprocess whose combined output is readable while it runs
# ---------------------------------------------------------------------------


class _Chain:
    """One `wake/claude.sh` subprocess with its stdout+stderr pumped into a
    buffer by a reader thread, so the fixture can react to a specific log
    line WHILE the chain is still running (the mechanism every overlap
    below depends on)."""

    def __init__(self, name: str, kanban_root: pathlib.Path, env: dict) -> None:
        self.name = name
        self.proc = subprocess.Popen(
            ["bash", str(kanban_root / _WAKE_CLAUDE_REL), f"--agent={_AGENT}"],
            cwd=str(kanban_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._lines: list[str] = []
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in iter(self.proc.stdout.readline, ""):
            with self._lock:
                self._lines.append(line)
        self.proc.stdout.close()

    def output(self) -> str:
        with self._lock:
            return "".join(self._lines)

    def wait_for(self, needle: str, *, timeout: float) -> None:
        """Block until *needle* appears in this chain's output. Fails fast,
        with the output attached, if the chain exits first without it."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if needle in self.output():
                return
            if self.proc.poll() is not None:
                # Drain whatever the reader thread still has, then give the
                # exited chain one last look before failing.
                self._thread.join(timeout=5)
                if needle in self.output():
                    return
                raise AssertionError(
                    f"chain {self.name} exited (rc={self.proc.returncode}) before "
                    f"logging {needle!r}.\nchain {self.name} output:\n{self.output()}"
                )
            time.sleep(0.1)
        raise AssertionError(
            f"chain {self.name} never logged {needle!r} within {timeout}s.\n"
            f"chain {self.name} output:\n{self.output()}"
        )

    def wait_exit(self, *, timeout: float) -> int:
        rc = self.proc.wait(timeout=timeout)
        self._thread.join(timeout=10)
        return rc

    def kill_if_running(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass


# ---------------------------------------------------------------------------
# The fixture
# ---------------------------------------------------------------------------


def test_lock_held_through_agent_sibling_defers_and_losing_chain_abandons(
    tmp_path: pathlib.Path,
) -> None:
    home_dir = tmp_path / "homedir"
    kanban_root = home_dir / "pgai_agent_kanban"
    tmp_scratch = tmp_path / "kanban_scratch"
    _build_kanban_root(kanban_root, _PROJECT, [_TASK_L1, _TASK_W1], tmp_scratch=tmp_scratch)
    lock_file = _lock_path(kanban_root, _PROJECT)

    stubs = tmp_path / "stubs"
    _install_delayed_stub(stubs / "L-bin", stubs / "L-control", _L_AGENT_SECONDS)
    _install_delayed_stub(stubs / "W-bin", stubs / "W-control", _W_AGENT_SECONDS)
    # S must never dispatch anything; its stub carries no delay and, if it
    # ever ran, would leave call evidence under S-control.
    install_claude_provider_stub(stubs / "S-bin", stubs / "S-control")

    chain_l: _Chain | None = None
    chain_w: _Chain | None = None
    try:
        # --- 1. Chain L takes the lock and dispatches the lock-holder task (L1) (long stub). ---
        chain_l = _Chain("L", kanban_root, _wake_env(kanban_root, stubs / "L-bin"))
        chain_l.wait_for(f"task {_TASK_L1}: transitioned to WORKING", timeout=60)
        assert lock_file.exists(), (
            f"chain L must hold the lock file while its subagent runs.\n"
            f"chain L output:\n{chain_l.output()}"
        )
        l_pid = _lock_pid(lock_file)
        assert l_pid, f"chain L's provenance must name its pid.\n{lock_file.read_text()}"
        # The chain took the lock with task=none; dispatching L1 names it,
        # and the acquisition time recorded with the lock is kept.
        assert _lock_field(lock_file, "task") == _TASK_L1, (
            f"chain L's provenance must name the task it dispatched.\n{lock_file.read_text()}"
        )
        assert _lock_field(lock_file, "acquired_at"), (
            f"chain L's provenance must keep its acquired_at.\n{lock_file.read_text()}"
        )

        # --- 2. Seed the loss: an external reap pulls L's file out from
        #        under it; chain W acquires the free path for real. ---
        lock_file.unlink()
        chain_w = _Chain("W", kanban_root, _wake_env(kanban_root, stubs / "W-bin"))
        chain_w.wait_for(f"task {_TASK_W1}: transitioned to WORKING", timeout=60)
        assert lock_file.exists(), (
            f"chain W must hold the lock file while its subagent runs.\n"
            f"chain W output:\n{chain_w.output()}"
        )
        w_pid = _lock_pid(lock_file)
        assert w_pid and w_pid != l_pid, (
            f"chain W's provenance must name W's own pid (L was {l_pid!r}).\n"
            f"{lock_file.read_text()}"
        )
        assert _lock_field(lock_file, "task") == _TASK_W1, (
            f"chain W's provenance must name the task it dispatched.\n{lock_file.read_text()}"
        )
        w_inode = lock_file.stat().st_ino

        # --- 3. L's subagent finishes; L stops at its merge boundary and
        #        must abandon -- leaving W's file exactly as W wrote it. ---
        l_rc = chain_l.wait_exit(timeout=_SUBPROCESS_TIMEOUT_SECONDS)
        l_out = chain_l.output()
        assert l_rc == 0, (
            f"a lost lock is a clean stop, not a crash: chain L must exit 0 "
            f"(got {l_rc}).\nchain L output:\n{l_out}"
        )
        assert "error:   lock.provenance.reverify_lost" in l_out and "'merge'" in l_out, (
            f"chain L must narrate the loss at the merge phase boundary.\n"
            f"chain L output:\n{l_out}"
        )
        assert f"task {_TASK_L1}: chain stopping before finalize/merge-verification" in l_out
        assert "abandoning this process's own fd without touching the lock file" in l_out, (
            f"chain L must reach run_project_chain's end-of-chain abandon "
            f"instead of dying at the merge-boundary return.\n"
            f"chain L output:\n{l_out}"
        )
        assert "pgai_lock_release_all_held" not in l_out, (
            f"chain L's exit sweep must have nothing left to release after "
            f"the abandon.\nchain L output:\n{l_out}"
        )
        assert lock_file.exists() and lock_file.stat().st_ino == w_inode, (
            f"chain L's exit must leave chain W's lock file (inode {w_inode}) "
            f"in place; it is gone or replaced.\nchain L output:\n{l_out}"
        )
        assert _lock_pid(lock_file) == w_pid, (
            f"chain W's provenance must survive chain L's exit untouched.\n"
            f"lock file:\n{lock_file.read_text()}\nchain L output:\n{l_out}"
        )
        assert chain_w.proc.poll() is None, (
            f"chain W must still be running (its subagent has "
            f"{_W_AGENT_SECONDS}s of work); it exited early.\n"
            f"chain W output:\n{chain_w.output()}"
        )

        # --- 4. Chain S wakes beside the live holder W and must defer. ---
        s_result = subprocess.run(
            ["bash", str(kanban_root / _WAKE_CLAUDE_REL), f"--agent={_AGENT}"],
            cwd=str(kanban_root),
            env=_wake_env(kanban_root, stubs / "S-bin"),
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
        s_out = s_result.stdout + s_result.stderr
        assert s_result.returncode == 0, f"chain S exited non-zero.\nchain S output:\n{s_out}"
        assert f"repo lock held by holder={_AGENT} pid={w_pid}" in s_out and "deferring" in s_out, (
            f"chain S must log the healthy deferring path naming chain W as "
            f"the holder.\nchain S output:\n{s_out}"
        )
        assert f"starting (agent={_AGENT}" not in s_out, (
            f"chain S must never enter the project chain while W holds the "
            f"lock.\nchain S output:\n{s_out}"
        )
        assert not (stubs / "S-control" / "call_count").exists(), (
            "chain S must never have dispatched a subagent"
        )

        # --- 5. W's finalize completes on this first dispatch. ---
        w_rc = chain_w.wait_exit(timeout=_SUBPROCESS_TIMEOUT_SECONDS)
        w_out = chain_w.output()
        assert w_rc == 0, f"chain W exited non-zero.\nchain W output:\n{w_out}"
        assert f"finished task {_TASK_W1} with state DONE" in w_out, (
            f"chain W's finalize must run for {_TASK_W1} on the first dispatch.\n"
            f"chain W output:\n{w_out}"
        )
        assert "repo lock ownership lost" not in w_out and "reverify_lost" not in w_out, (
            f"chain W must never observe a loss: it held the lock throughout.\n"
            f"chain W output:\n{w_out}"
        )
        assert f"stale-active: {_TASK_W1}" not in w_out and "resetting to [ ]/BACKLOG" not in w_out, (
            f"no stale-active reset may touch {_TASK_W1}.\nchain W output:\n{w_out}"
        )
        assert f"- [x] {_TASK_W1}" in _queue_text(kanban_root), (
            f"{_TASK_W1}'s [A] marker must have become [x].\n"
            f"queue:\n{_queue_text(kanban_root)}\nchain W output:\n{w_out}"
        )
        assert "## State\nDONE" in _task_status_text(kanban_root, _TASK_W1)
        assert f"- [A] {_TASK_W1}" not in _queue_text(kanban_root)

        # W released its own lock normally at its chain's end; nothing
        # survives on disk.
        assert not lock_file.exists(), (
            "the lock file must not survive chain W's own end-of-chain release"
        )
    finally:
        for chain in (chain_l, chain_w):
            if chain is not None:
                chain.kill_if_running()
