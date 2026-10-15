"""
test_chain_lock_reverification.py
===================================
End-to-end coverage for chain-level repo lock ownership reverification: a
chain re-verifies it still holds its project's repo lock before every
mutating phase boundary
(agent dispatch, the post-agent merge check, the queue-marker write --
team/scripts/lib/wake_driver.sh's process_one_task and
team/scripts/lib/wake_driver_finalize.sh's wd_finalize_task), and stops
cleanly, before performing that phase, the moment it no longer does.

Scenario driven end-to-end through the REAL production entry point (a real
`bash scripts/wake/claude.sh --agent=writer` subprocess, following the same
"extraction and harness" convention as test_wake_contention_retry.py and
test_provider_runtime_integration.py):

  1. Chain A starts against a project queue holding two tasks, task A1 and
     task A2. It acquires the project's repo lock and dispatches task A1
     through a PATH-shadowing `claude` stub (tests/fixtures/claude_provider_
     stub.py) that marks it DONE -- no real CLI, no network, no token.
  2. Once task A1 finishes (observed via chain A's own "finished task"
     log line, streamed in real time), this fixture simulates an external
     stale-lock reap: it deletes the project's repo lock file out from
     under chain A -- exactly what team/scripts/release-locks.sh --force
     or an over-aggressive age threshold would do -- and seats a KNOWN
     holder on the freed path: a real flock taken by this test process,
     carrying a complete four-field provenance record (holder/pid/op/task)
     the test chose. Chain A is held in its pause between tasks until
     both are done (see "Ordering" below), so it cannot reach its next
     phase boundary first however loaded the host is.
  3. Chain A, still running, loops back to what it believes is its next
     task (task A2) and hits the dispatch-boundary reverification. It must
     stop cleanly there -- never dispatching task A2, never writing its
     queue marker, never touching its status.md -- and narrate the loss
     dual-channel, naming the known holder's four fields.
  4. Only once chain A's narration has been observed does the fixture
     release the known holder (unlink, then close -- the same order
     pgai_lock_release uses) and run chain B, a genuinely SEPARATE
     `wake/claude.sh` subprocess against the SAME project and the SAME
     shared writer queue. Chain B acquires the now-free lock (a real,
     independent flock acquisition) and picks up task A2, the one task
     chain A never touched, dispatching and completing it.

Why the holder is seated by the fixture rather than left to chain B: what
chain A's narration names depends only on what sits at the lock path at
the instant chain A reverifies. Had chain B's own startup been left to
fill that path, the narration would name chain B, nobody (path unlinked,
chain B not yet acquired), or nobody again (chain B already released),
depending on host load. Seating a known holder makes the narrated holder
a value this test controls.

Ordering: the reap and the seat must both land after task A1's own
finalize boundaries (so task A1 really is processed under unbroken
ownership) and before chain A's 'agent dispatch' reverification for the
next task. The only thing chain A does in between is its pause between
tasks, `sleep "$PAUSE_BETWEEN_TASKS"` in team/scripts/lib/wake_driver.sh.
Chain A therefore runs with a PATH-shadowing `sleep` (_install_pause_gate)
that recognises exactly that pause's argument and, instead of sleeping
for a fixed time, waits until this test opens the gate -- which it does
only after the holder is seated. Every other `sleep` call passes through
to the real binary. The order is enforced by that handshake, not by how
quickly this process reacts to streamed output, so host load and test
order cannot change the verdict.

Behaviors exercised:
  - Chain A stops at its next phase boundary (dispatch) before performing
    it, once its lock is pulled out from under it.
  - The stop is narrated dual-channel with a stable error key and names the
    current holder's holder/pid/op/task fields, read fresh at the moment
    of loss.
  - Chain B reaches task A2 and chain A does not: exactly one writer (chain
    B) touches task A2's status.md and queue marker.
  - The healthy path (task A1, processed entirely under chain A's own
    unbroken lock ownership) passes every phase boundary with no behavior
    change and no extra narration.

Design notes
------------
- Every kanban root built here is a full, self-contained copy of this dev
  tree's scripts/, pgai_agent_kanban/, workflows/, pm-agent/, and config/
  directories, mirroring test_provider_runtime_integration.py's own
  kanban-root builder.
- The project uses the document workflow (git_mode=none): no dev tree or
  git repository is required, since the reverification boundaries under
  test (dispatch, queue write) fire identically regardless of git_mode --
  only the "merge" boundary's own dev-tree mutations are git-workflow
  specific, and this fixture does not need to exercise that boundary
  separately to prove the reverification (the dispatch-boundary catch below already
  demonstrates a chain stopping itself before any further shared-state
  mutation, which is the property this reverification exists to guarantee).
- Chain A's PAUSE_BETWEEN_TASKS is set to _PAUSE_GATE_SECONDS, the one
  argument its gated `sleep` intercepts; no other sleep in the wake path
  uses that value. The gate has a deadline of its own so a test that
  never opens it fails with the gate's own message instead of hanging.
  Chain B runs without the gate, after chain A's narration, so its
  startup time cannot change what chain A observes either.
- All temp paths use pytest's tmp_path. No bare /tmp.
"""

from __future__ import annotations

import fcntl
import os
import pathlib
import shlex
import shutil
import subprocess
import time

import pytest

from tests.fixtures.claude_provider_stub import install_claude_provider_stub

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_WAKE_CLAUDE_REL = pathlib.Path("scripts") / "wake" / "claude.sh"
_AGENT = "writer"
_SUBPROCESS_TIMEOUT_SECONDS = 60

# Host env vars this suite's own live-wake context may have exported --
# stripped from every subprocess env built below (docs/test-authoring-
# guidelines.md, Anti-pattern 2), mirroring test_provider_runtime_
# integration.py's own _LEAK_PREFIXES.
_LEAK_PREFIXES = (
    "PGAI_", "PP_", "KANBAN_ROOT", "CLAUDE_", "AI_AGENT", "STOP_FILE",
    "TMPDIR", "MAX_TASK_SECONDS", "MAX_RUNTIME_SECONDS", "MAX_TASKS_PER_WAKE",
    "MAX_PROJECT_SECONDS", "KILL_GRACE_SECONDS", "PAUSE_BETWEEN_TASKS",
    "STOP_ON_BLOCKED", "AI_AUTH_MODE", "BASH_MAX_TIMEOUT_MS", "DASHBOARD_",
    "PM_MAX_TASKS",
)


def _clean_base_env() -> dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if not any(k == p or k.startswith(p) for p in _LEAK_PREFIXES)
    }


# ---------------------------------------------------------------------------
# Kanban root / task builders
# ---------------------------------------------------------------------------


def _write_stub_task(tasks_dir: pathlib.Path, task_id: str) -> None:
    """A minimal WRITER/document-workflow task README + BACKLOG status.md --
    document workflow (git_mode=none) means no worktree is created, so a
    task can move BACKLOG -> WORKING -> DONE through nothing but the
    provider stub, exactly like test_provider_runtime_integration.py's own
    stub tasks."""
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
        "[wake]\nmax_tasks_per_wake = 5\n",
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
    run_project_chain acquires for *project_name* (see
    test_wake_contention_retry.py's own identical helper)."""
    repo_id = "".join(c for c in project_name.replace("/", "_") if c.isalnum() or c in "_-")
    return kanban_root / "locks" / f"repo-wake-{repo_id}.lock"


# The known holder this fixture seats on the reaped lock path. Its pid is
# this test process's own -- alive for the whole test, and never chain A's
# pid, so chain A's reverification cannot mistake the record for its own.
_KNOWN_HOLDER = {
    "holder": "stale-lock-reaper",
    "pid": str(os.getpid()),
    "op": "reap-and-hold",
    "task": "none",
}


def _seat_known_holder(lock_file: pathlib.Path) -> int:
    """Take a real exclusive flock on *lock_file* (which must not exist)
    and record _KNOWN_HOLDER's four fields there, in the same
    record-and-lock-arrive-together order pgai_lock_acquire uses: stage the
    record in a private sibling, flock that inode, then link it onto the
    lock path (link fails rather than replacing an existing file). Returns
    the open fd, which keeps the flock held until _release_known_holder."""
    staged = lock_file.with_name(f"{lock_file.name}.tmp.{os.getpid()}")
    record = "".join(f"{key}={value}\n" for key, value in _KNOWN_HOLDER.items())
    fd = os.open(str(staged), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        os.write(fd, record.encode("utf-8"))
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.link(str(staged), str(lock_file))
    except BaseException:
        os.close(fd)
        raise
    finally:
        staged.unlink(missing_ok=True)
    return fd


# The between-tasks pause chain A is given. Chain A's gated `sleep`
# intercepts exactly this argument; no other sleep in the wake path uses it
# (they sleep for 1 second, or for the --sleep stagger, which defaults to 0).
_PAUSE_GATE_SECONDS = "3"


def _install_pause_gate(bin_dir: pathlib.Path, gate_dir: pathlib.Path) -> None:
    """Install a PATH-shadowing ``sleep`` into *bin_dir* that turns chain A's
    pause between tasks into a handshake with this test.

    ``sleep <_PAUSE_GATE_SECONDS>`` touches *gate_dir*/entered, then waits
    until *gate_dir*/open exists and returns 0 -- it never sleeps a fixed
    time. A gate nobody opens within _SUBPROCESS_TIMEOUT_SECONDS exits 1
    with a message naming the file it waited for, so a broken handshake
    fails loudly instead of hanging. Any other argument list is handed to
    the real ``sleep`` unchanged."""
    real_sleep = shutil.which("sleep")
    assert real_sleep, "the pause gate needs a real `sleep` binary on PATH to delegate to"
    gate_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    entered = shlex.quote(str(gate_dir / "entered"))
    opened = shlex.quote(str(gate_dir / "open"))
    real = shlex.quote(real_sleep)
    script = (
        "#!/bin/bash\n"
        "# Test-only sleep stand-in: holds the chain's pause between tasks\n"
        "# until the test opens the gate; every other call is a real sleep.\n"
        f'if [[ $# -eq 1 && "$1" == "{_PAUSE_GATE_SECONDS}" ]]; then\n'
        f"    : > {entered}\n"
        f"    deadline=$(( SECONDS + {_SUBPROCESS_TIMEOUT_SECONDS} ))\n"
        f"    while [[ ! -e {opened} ]]; do\n"
        "        if (( SECONDS >= deadline )); then\n"
        f'            echo "pause gate: nothing created {gate_dir / "open"} within '
        f'{_SUBPROCESS_TIMEOUT_SECONDS}s; giving up so the chain does not hang" >&2\n'
        "            exit 1\n"
        "        fi\n"
        f"        {real} 0.05\n"
        "    done\n"
        "    exit 0\n"
        "fi\n"
        f'exec {real} "$@"\n'
    )
    gate = bin_dir / "sleep"
    gate.write_text(script, encoding="utf-8")
    gate.chmod(0o755)


def _open_pause_gate(gate_dir: pathlib.Path) -> None:
    """Let chain A's held pause between tasks return. Idempotent."""
    (gate_dir / "open").touch()


def _release_known_holder(lock_file: pathlib.Path, fd: int) -> None:
    """Drop the known holder: unlink the path first, then close the fd
    (releasing the flock) -- pgai_lock_release's own order, so no reader
    ever sees the path without a held lock behind it."""
    lock_file.unlink(missing_ok=True)
    os.close(fd)


def _task_status_text(kanban_root: pathlib.Path, project_name: str, task_id: str) -> str:
    return (kanban_root / "projects" / project_name / "tasks" / task_id / "status.md").read_text(
        encoding="utf-8"
    )


def _wake_env(
    kanban_root: pathlib.Path,
    stub_bin_dir: pathlib.Path,
    *,
    pause_gate_bin: pathlib.Path | None = None,
    extra: dict | None = None,
) -> dict:
    """Wake subprocess environment. With *pause_gate_bin*, that directory's
    gated ``sleep`` (_install_pause_gate) is put first on PATH and the pause
    between tasks is set to the one value it intercepts."""
    env = _clean_base_env()
    env["PATH"] = f"{stub_bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["PGAI_TASKS_DIR"] = str(kanban_root / "tasks")
    env["HOME"] = str(kanban_root.parent)
    # A short real pause for an ungated chain.
    env["PAUSE_BETWEEN_TASKS"] = "2"
    if pause_gate_bin is not None:
        env["PATH"] = f"{pause_gate_bin}{os.pathsep}{env['PATH']}"
        env["PAUSE_BETWEEN_TASKS"] = _PAUSE_GATE_SECONDS
    if extra:
        env.update(extra)
    return env


def _run_wake_streaming(cmd: list[str], *, cwd: pathlib.Path, env: dict, on_line, timeout: float) -> tuple[int, str]:
    """Run *cmd* as a subprocess, calling on_line(line) for every line of
    its combined stdout/stderr as it is produced, in real time -- mirrors
    test_wake_contention_retry.py's own helper of the same name.

    *timeout* bounds *cmd*'s own run: time spent inside on_line is not
    charged to it, because a callback may run a whole second chain under
    that chain's own timeout, and charging both runs to one budget would
    make the verdict track host load."""
    proc = subprocess.Popen(
        cmd, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    collected: list[str] = []
    start = time.monotonic()
    in_callback = 0.0
    try:
        for line in iter(proc.stdout.readline, ""):
            collected.append(line)
            callback_start = time.monotonic()
            on_line(line)
            in_callback += time.monotonic() - callback_start
            if time.monotonic() - start - in_callback > timeout:
                raise subprocess.TimeoutExpired(cmd, timeout)
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
    returncode = proc.wait(timeout=timeout)
    return returncode, "".join(collected)


# ---------------------------------------------------------------------------
# The fixture
# ---------------------------------------------------------------------------


def test_reaped_chain_stops_before_dispatch_second_chain_reaches_the_task(
    tmp_path: pathlib.Path,
) -> None:
    project = "shared-project"
    task_a1 = "WRITER-20260101-001-reverify-first"
    task_a2 = "WRITER-20260101-002-reverify-second"

    home_dir = tmp_path / "homedir"
    kanban_root = home_dir / "pgai_agent_kanban"
    tmp_scratch = tmp_path / "kanban_scratch"
    _build_kanban_root(kanban_root, project, [task_a1, task_a2], tmp_scratch=tmp_scratch)

    stub_bin_dir = tmp_path / "stub-bin"
    control_dir = tmp_path / "stub-control"
    install_claude_provider_stub(stub_bin_dir, control_dir)

    lock_file = _lock_path(kanban_root, project)

    pause_gate_bin = tmp_path / "pause-gate-bin"
    pause_gate_dir = tmp_path / "pause-gate"
    _install_pause_gate(pause_gate_bin, pause_gate_dir)

    chain_b_result: dict = {}
    held = {"fd": None}
    phase = {"reaped": False, "chain_b_ran": False}

    def _run_chain_b() -> None:
        # Chain B: a genuinely separate wake/claude.sh subprocess against
        # the SAME project and the SAME shared writer queue. It acquires
        # the freed lock for real (not a simulated takeover) and picks up
        # task A2, the only task chain A has not touched.
        phase["chain_b_ran"] = True
        chain_b_result["proc"] = subprocess.run(
            ["bash", str(kanban_root / _WAKE_CLAUDE_REL), f"--agent={_AGENT}"],
            cwd=str(kanban_root),
            env=_wake_env(kanban_root, stub_bin_dir),
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )

    def _on_line(line: str) -> None:
        if not phase["reaped"]:
            if f"finished task {task_a1} with state DONE" not in line:
                return
            phase["reaped"] = True
            # Simulate an external stale-lock reap: chain A's lock file is
            # pulled out from under it. A real reaper (pgai_lock_acquire's
            # own contention-path reaper, or an operator's release-locks.sh
            # --force) does exactly this -- remove the path so the next
            # acquirer's flock succeeds regardless of chain A's still-open
            # fd (see lock_provenance.sh's "Crash recovery" header section).
            # Chain A is held in its gated pause between tasks (or has not
            # reached it yet) until the gate opens below, so the record it
            # reads at its dispatch boundary is the one this test seats.
            assert lock_file.exists(), "chain A must hold the lock file at this point"
            lock_file.unlink()
            held["fd"] = _seat_known_holder(lock_file)
            _open_pause_gate(pause_gate_dir)
            return
        if phase["chain_b_ran"] or "lock.provenance.reverify_lost" not in line:
            return
        # Chain A has read the current holder and narrated the loss; the
        # known holder has served its purpose. Release it and let chain B
        # take the lock and the remaining task.
        _release_known_holder(lock_file, held["fd"])
        held["fd"] = None
        _run_chain_b()

    chain_a_env = _wake_env(kanban_root, stub_bin_dir, pause_gate_bin=pause_gate_bin)
    try:
        chain_a_returncode, chain_a_output = _run_wake_streaming(
            ["bash", str(kanban_root / _WAKE_CLAUDE_REL), f"--agent={_AGENT}"],
            cwd=kanban_root,
            env=chain_a_env,
            on_line=_on_line,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
    finally:
        # Never leave chain A parked in its pause if this test bailed out
        # before opening the gate itself.
        _open_pause_gate(pause_gate_dir)
        # Chain A ended without narrating a loss (the stop regressed): drop
        # the known holder so nothing outlives the test.
        if held["fd"] is not None:
            _release_known_holder(lock_file, held["fd"])
            held["fd"] = None

    if "proc" in chain_b_result:
        chain_b_output = chain_b_result["proc"].stdout + chain_b_result["proc"].stderr
    else:
        chain_b_output = "(chain B never ran: chain A never narrated the lost lock)\n"
    both_outputs = f"chain A output:\n{chain_a_output}\nchain B output:\n{chain_b_output}"

    assert phase["reaped"], (
        f"never observed chain A finishing {task_a1}; the reap never ran.\n{both_outputs}"
    )
    assert (pause_gate_dir / "entered").exists(), (
        f"chain A never paused through the gated `sleep {_PAUSE_GATE_SECONDS}` between "
        f"tasks, so nothing ordered the reap before its dispatch reverification; "
        f"check that wake_driver.sh still pauses with `sleep \"$PAUSE_BETWEEN_TASKS\"` "
        f"and that PAUSE_BETWEEN_TASKS reached it.\n{both_outputs}"
    )

    # --- The safety property: chain A never dispatched task A2 after
    #     losing the lock, and stopped cleanly. Checked before anything
    #     about chain B, so a regression here is reported as itself. ---
    assert f"task {task_a2}: transitioned to WORKING" not in chain_a_output, (
        f"chain A must never have dispatched {task_a2} after losing the lock.\n{both_outputs}"
    )
    assert f"finished task {task_a2}" not in chain_a_output, (
        f"chain A must never have finished/finalized {task_a2}.\n{both_outputs}"
    )
    assert chain_a_returncode == 0, (
        f"chain A must exit 0 (a lost lock is a clean stop, not a crash).\n{both_outputs}"
    )

    # --- The stop is narrated dual-channel, naming the current holder. ---
    assert "error:   lock.provenance.reverify_lost" in chain_a_output, (
        f"expected chain A's own narration of the lost lock.\n{both_outputs}"
    )
    expected_holder = " ".join(f"{key}={value}" for key, value in _KNOWN_HOLDER.items())
    assert f"it is now held by {expected_holder}" in chain_a_output, (
        f"expected the narration to name the known holder seated on the "
        f"reaped lock ({expected_holder}).\n{both_outputs}"
    )
    assert "'agent dispatch'" in chain_a_output, (
        f"expected the narration to name the dispatch phase boundary.\n{both_outputs}"
    )
    assert "(op=wake-chain:shared-project, task=none)" in chain_a_output, (
        f"expected the narration to name chain A's own op/task fields "
        f"(no task is selected yet at the dispatch boundary).\n{both_outputs}"
    )
    assert "chain lost repo lock ownership mid-run" in chain_a_output, (
        f"expected chain A's own per-project summary line to name the "
        f"stop reason.\n{both_outputs}"
    )

    # --- Chain B reached task A2 once the lock was free. ---
    assert "proc" in chain_b_result, f"chain B was never started.\n{both_outputs}"
    assert chain_b_result["proc"].returncode == 0, f"chain B exited non-zero.\n{both_outputs}"
    assert "## State\nDONE" in _task_status_text(kanban_root, project, task_a2), (
        f"chain B must have completed {task_a2}.\n{both_outputs}"
    )
    assert f"finished task {task_a2}" in chain_b_output, (
        f"expected chain B's own log to record finishing {task_a2}.\n{both_outputs}"
    )

    # --- task A1 (processed entirely under chain A's own unbroken
    #     ownership) passed every boundary with no extra narration tied to
    #     it, and reached DONE normally. ---
    assert "## State\nDONE" in _task_status_text(kanban_root, project, task_a1)
    assert f"finished task {task_a1} with state DONE" in chain_a_output

    # --- Chain A abandoned its own fd without corrupting the record seated
    #     after the reap; chain B released normally at its own chain's end,
    #     so no lock file survives either chain. ---
    assert not lock_file.exists(), (
        "the lock file must not survive both chains' end-of-chain cleanup "
        f"(chain A abandons without writing; chain B releases normally).\n{both_outputs}"
    )
