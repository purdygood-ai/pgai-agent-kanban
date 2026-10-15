"""
test_wake_stop_file_stops_batch.py
==================================
End-to-end coverage for the operator stop file as a batch-level stop
condition: touching it stops every running wake batch -- every agent, every
registered project -- at its next task boundary, and the file stays on disk
until the operator removes it.

Every test here drives the REAL production entry point (`bash
scripts/wake/claude.sh --agent=...`) as a subprocess against a throwaway
kanban root with four registered document-workflow projects (git_mode=none,
so no dev tree or git repository is needed). Task dispatch is real: each
project queues one BACKLOG task for either the writer or the tester, and a
PATH-shadowing `claude` stub completes it the way
tests/fixtures/claude_provider_stub.py does (writes DONE into the task's
status.md located from the real prompt text, prints a valid
--output-format json payload). The stub never reaches a real model.

Coverage
--------
  1. test_stop_file_touched_mid_task_stops_both_agents_batches_and_persists
     -- a writer batch and a tester batch run concurrently over the same
     four projects. The writer's first task touches the stop file while
     the tester's first task is still running. Afterwards: the writer's
     first project stopped with a reason naming the file, the writer's
     later projects dispatched nothing, the tester's in-flight task ran to
     completion and its later projects dispatched nothing, the tester's
     end-of-iteration contention retry pass (it was contended on the
     writer's project) performed no retry, and the stop file is still on
     disk.
  2. test_batch_started_while_stop_file_exists_dispatches_nothing_until_operator_removes_it
     -- a batch that starts while the file already exists dispatches
     nothing and exits 0 naming the file; removing the file (the
     operator's action) lets the next batch dispatch normally.

Choreography (test 1)
---------------------
Project order (projects.cfg priority) is writer-first, tester-first,
writer-second, tester-second. The stub keys its behaviour on the
PGAI_PROJECT_NAME the wake exports into the agent process:

  writer-first  -- marks itself started, waits for tester-first to start,
                   then touches the stop file and completes.
  tester-first  -- marks itself started, waits for the stop file to
                   appear, then completes.
  anything else -- completes immediately.

The test launches the writer batch, waits for the writer-first marker (the
writer chain now holds writer-first's repo lock and is mid-task), then
launches the tester batch. The tester is therefore contended on
writer-first (deferred to its retry pass) and dispatches at tester-first,
whose stub turn is still waiting when the writer's turn touches the file --
so both batches are genuinely mid-task when the stop lands, and every
assertion below is about what each batch does at its NEXT boundary. Every
wait in the stub is bounded, so a broken sequence fails an assertion
instead of hanging.

Design notes
------------
- The kanban root is a full, self-contained copy of this dev tree's
  scripts/, pgai_agent_kanban/, workflows/, pm-agent/, and config/ (the set
  install.sh mirrors into a live install), so the wake script under test is
  byte-identical to production and self-locates as it would on a host.
- The subprocess environment is built from an explicit minimal set of
  names (tests.fixtures.scrubbed_subprocess_env.build_scrubbed_env), never
  inherited, so a host operator's own STOP_FILE / wake tunables cannot
  leak in.
- All scratch lives under pytest's tmp_path. No bare /tmp.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import stat
import subprocess
import sys
import textwrap
import time

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_WAKE_CLAUDE_REL = pathlib.Path("scripts") / "wake" / "claude.sh"
_MIRRORED_DIRS = ("scripts", "pgai_agent_kanban", "workflows", "pm-agent", "config")

# (project name, agent whose backlog carries that project's one task), in
# projects.cfg priority order -- see "Choreography" in the module docstring.
_PROJECTS = (
    ("writer-first", "writer"),
    ("tester-first", "tester"),
    ("writer-second", "writer"),
    ("tester-second", "tester"),
)
_ALL_AGENTS = ("pm", "writer", "tester", "cm")

# Longest any single stub turn will wait for its counterpart before giving
# up (and recording why). The per-task watchdog below is set above this so
# a slow-but-correct sequence is never mistaken for a hung task.
_STUB_WAIT_SECONDS = 30
_PER_TASK_WATCHDOG_SECONDS = 90
_SUBPROCESS_TIMEOUT_SECONDS = 180
_MARKER_WAIT_SECONDS = 60


# ---------------------------------------------------------------------------
# Fixture-tree builders
# ---------------------------------------------------------------------------


def _task_id(project_name: str, agent: str) -> str:
    return f"{agent.upper()}-20260101-001-{project_name}"


def _write_task(tasks_dir: pathlib.Path, task_id: str, role: str) -> None:
    """A minimal document-workflow task (git_mode=none: no worktree, and the
    writer completion gate passes trivially) queued BACKLOG."""
    task_dir = tasks_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "README.md").write_text(
        f"# Task: {task_id}\n\n"
        f"## Task ID\n{task_id}\n\n"
        f"## Role\n{role}\n\n"
        "## Workflow Type\ndocument\n\n"
        "## Working Directory\nnone\n\n"
        "## Source Branch\nnone\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{task_id}\n\n## State\nBACKLOG\n\n## Blockers\nnone\n",
        encoding="utf-8",
    )


def _build_kanban_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Register the four _PROJECTS as document-workflow projects, each with
    one BACKLOG task in the backlog of the agent named beside it and empty
    backlogs for every other agent."""
    root = tmp_path / "kanban_root"
    root.mkdir(parents=True, exist_ok=True)
    scratch = tmp_path / "kanban_scratch"
    scratch.mkdir(parents=True, exist_ok=True)

    (root / "kanban.cfg").write_text(
        "[paths]\n"
        f"tmp_root = {scratch}\n"
        "tmp_subdir = pgai_kanban_tmp\n\n"
        "[chain]\npm_mode = automatic\n\n"
        "[release]\nmax_rc_retries = 3\n\n"
        "[wake]\nmax_tasks_per_wake = 5\n"
        # lock_wait_seconds = 0: the contention under test is a wake chain
        # holding the lock, which a wake otherwise waits up to 120 s for;
        # this fixture pins the immediate-deferral path.
        "lock_wait_seconds = 0\n",
        encoding="utf-8",
    )
    (root / "projects.cfg").write_text(
        "".join(
            f"[project:{name}]\npriority={i}\nenabled=true\n\n"
            for i, (name, _agent) in enumerate(_PROJECTS, start=1)
        ),
        encoding="utf-8",
    )

    for name, task_agent in _PROJECTS:
        proj = root / "projects" / name
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "project.cfg").write_text(
            "[project]\n"
            f"project_name = {name}\n"
            "workflow_type = document\n",
            encoding="utf-8",
        )
        queues_dir = proj / "tasks" / "queues"
        queues_dir.mkdir(parents=True, exist_ok=True)
        for agent in _ALL_AGENTS:
            if agent == task_agent:
                tid = _task_id(name, agent)
                _write_task(proj / "tasks", tid, agent.upper())
                body = f"# {name} {agent} backlog\n\n- [ ] {tid} — fixture task\n"
            else:
                body = f"# {name} {agent} backlog\n\nNo tasks queued by this fixture.\n"
            (queues_dir / f"{agent}_backlog.md").write_text(body, encoding="utf-8")

    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)
    for dirname in _MIRRORED_DIRS:
        shutil.copytree(_TEAM_DIR / dirname, root / dirname)
    return root


def _install_stub_provider(
    tmp_path: pathlib.Path,
    *,
    stop_file: pathlib.Path,
    mode: str,
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """Write the PATH-shadowing `claude` stub. Returns (bin_dir, control_dir,
    sync_dir). *mode* is "choreograph" (the two-turn handshake described in
    the module docstring) or "plain" (every turn completes at once).

    Every dispatch appends "<project> <task-id>" to control_dir/dispatches.log,
    the ledger the tests read to prove which projects dispatched and which
    never did. A bounded wait that expires is recorded in
    control_dir/stub-warnings.log rather than hanging the turn.
    """
    bin_dir = tmp_path / "stub_provider_bin"
    control_dir = tmp_path / "stub_control"
    sync_dir = tmp_path / "stub_sync"
    for d in (bin_dir, control_dir, sync_dir):
        d.mkdir(parents=True, exist_ok=True)
    (control_dir / "mode").write_text(mode + "\n", encoding="utf-8")

    script = textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        # Test-only stand-in for the provider CLI: completes the dispatched
        # task and, in choreograph mode, drives the stop-file handshake the
        # test describes. Never reaches a real model.
        set -u
        CONTROL_DIR={str(control_dir)!r}
        SYNC_DIR={str(sync_dir)!r}
        STOP_FILE_PATH={str(stop_file)!r}
        WAIT_SECONDS={_STUB_WAIT_SECONDS}

        for _arg in "$@"; do
          if [[ "$_arg" == "--version" ]]; then
            echo "stop-file-fixture-stub 0.0.0"
            exit 0
          fi
        done

        mkdir -p "$CONTROL_DIR" "$SYNC_DIR"
        project="${{PGAI_PROJECT_NAME:-<unset>}}"
        task="${{PGAI_TASK_ID:-<unset>}}"
        echo "$project $task" >> "$CONTROL_DIR/dispatches.log"
        mode="plain"
        [[ -f "$CONTROL_DIR/mode" ]] && mode="$(cat "$CONTROL_DIR/mode")"

        wait_for() {{
          local path="$1" deadline=$(( SECONDS + WAIT_SECONDS ))
          while [[ ! -e "$path" && $SECONDS -lt $deadline ]]; do sleep 0.05; done
          [[ -e "$path" ]]
        }}

        if [[ "$mode" == "choreograph" ]]; then
          case "$project" in
            writer-first)
              : > "$SYNC_DIR/writer-first-started"
              wait_for "$SYNC_DIR/tester-first-started" \\
                || echo "writer-first: tester-first never started within ${{WAIT_SECONDS}}s" >> "$CONTROL_DIR/stub-warnings.log"
              mkdir -p "$(dirname "$STOP_FILE_PATH")"
              : > "$STOP_FILE_PATH"
              ;;
            tester-first)
              : > "$SYNC_DIR/tester-first-started"
              wait_for "$STOP_FILE_PATH" \\
                || echo "tester-first: stop file never appeared within ${{WAIT_SECONDS}}s" >> "$CONTROL_DIR/stub-warnings.log"
              ;;
          esac
        fi

        # Complete the task the way a real agent turn would: the status.md
        # path comes from the real prompt text ("Read the status at <path>",
        # emitted by _wd_build_task_prompt in wake_driver_invoke.sh).
        prompt=""
        _prev=""
        for _a in "$@"; do
          [[ "$_prev" == "-p" ]] && prompt="$_a"
          _prev="$_a"
        done
        status_path="$(printf '%s\\n' "$prompt" | grep -oE 'Read the status at [^[:space:]]+' | head -n1 | awk '{{print $NF}}')"
        if [[ -n "$status_path" && -f "$status_path" ]]; then
          cat > "$status_path" <<STATUSEOF
        # Status

        ## Task
        $task

        ## State
        DONE

        ## Summary
        Stub provider completed the task (integration test double; no real CLI invoked).

        ## Artifacts
        none

        ## Blockers
        none

        ## Needs Human
        no

        ## Next Recommended Step
        none

        ## Instruction Conflicts
        none
        STATUSEOF
        fi

        echo '{{"type":"result","result":"stub agent completed the task","usage":{{"input_tokens":10,"output_tokens":5,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}}}'
        exit 0
        """
    )
    stub_path = bin_dir / "claude"
    stub_path.write_text(script, encoding="utf-8")
    stub_path.chmod(stub_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # A private python3 resolving to this test process's own interpreter, in
    # a directory holding nothing else -- see
    # wake_entrypoint_acceptance_harness.build_stub_provider_cli for why a
    # private symlink beats adding the interpreter's directory to PATH.
    python3_link = bin_dir / "python3"
    if not python3_link.exists():
        python3_link.symlink_to(pathlib.Path(sys.executable).resolve())
    return bin_dir, control_dir, sync_dir


def _wake_env(
    tmp_path: pathlib.Path,
    root: pathlib.Path,
    *,
    stub_bin_dir: pathlib.Path,
    stop_file: pathlib.Path,
) -> dict:
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    return build_scrubbed_env(
        home_dir=home_dir,
        extra_path_dirs=(stub_bin_dir,),
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "kanban_scratch" / "pgai_kanban_tmp"),
            "STOP_FILE": str(stop_file),
            # No pause between tasks: the boundary after the first task is
            # what every assertion is about, not the sleep before it.
            "PAUSE_BETWEEN_TASKS": "0",
            "MAX_TASK_SECONDS": str(_PER_TASK_WATCHDOG_SECONDS),
            "KILL_GRACE_SECONDS": "5",
        },
    )


# ---------------------------------------------------------------------------
# Running the real entry point
# ---------------------------------------------------------------------------


def _wake_cmd(root: pathlib.Path, agent: str) -> list[str]:
    return ["bash", str(root / _WAKE_CLAUDE_REL), f"--agent={agent}"]


def _start_wake(root: pathlib.Path, agent: str, env: dict, output_path: pathlib.Path) -> subprocess.Popen:
    """Launch one wake batch in the background with its combined
    stdout/stderr streamed to *output_path* (a file, never a pipe, so two
    concurrent batches can never block each other on a full pipe)."""
    out = output_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        _wake_cmd(root, agent),
        cwd=str(root),
        env=env,
        stdout=out,
        stderr=subprocess.STDOUT,
        text=True,
    )
    proc._fixture_output_handle = out  # type: ignore[attr-defined]  # closed in _finish_wake
    return proc


def _finish_wake(proc: subprocess.Popen, output_path: pathlib.Path) -> tuple[int, str]:
    try:
        returncode = proc.wait(timeout=_SUBPROCESS_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
        returncode = -1
    finally:
        proc._fixture_output_handle.close()  # type: ignore[attr-defined]
    return returncode, output_path.read_text(encoding="utf-8")


def _wait_for_marker(marker: pathlib.Path, proc: subprocess.Popen, output_path: pathlib.Path) -> None:
    deadline = time.time() + _MARKER_WAIT_SECONDS
    while time.time() < deadline:
        if marker.exists():
            return
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    raise AssertionError(
        f"marker {marker} never appeared (wake process "
        f"{'exited ' + str(proc.returncode) if proc.poll() is not None else 'still running'}).\n"
        f"output so far:\n{output_path.read_text(encoding='utf-8')}"
    )


def _run_wake(root: pathlib.Path, agent: str, env: dict) -> tuple[int, str]:
    completed = subprocess.run(
        _wake_cmd(root, agent),
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )
    return completed.returncode, completed.stdout + completed.stderr


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


def _task_state(root: pathlib.Path, project_name: str, agent: str) -> str:
    status = root / "projects" / project_name / "tasks" / _task_id(project_name, agent) / "status.md"
    m = re.search(r"^##\s+State\s*\n+(.*?)(\n+##|\Z)", status.read_text(encoding="utf-8"), flags=re.S | re.M)
    return m.group(1).strip() if m else f"<no ## State in {status}>"


def _dispatched_projects(control_dir: pathlib.Path) -> list[str]:
    ledger = control_dir / "dispatches.log"
    if not ledger.exists():
        return []
    return [line.split()[0] for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]


def _stub_warnings(control_dir: pathlib.Path) -> str:
    path = control_dir / "stub-warnings.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ---------------------------------------------------------------------------
# Test 1: touched mid-task, two agents' batches running concurrently
# ---------------------------------------------------------------------------


def test_stop_file_touched_mid_task_stops_both_agents_batches_and_persists(tmp_path: pathlib.Path) -> None:
    root = _build_kanban_root(tmp_path)
    stop_file = tmp_path / "wakeup" / "stop"
    bin_dir, control_dir, sync_dir = _install_stub_provider(tmp_path, stop_file=stop_file, mode="choreograph")
    env = _wake_env(tmp_path, root, stub_bin_dir=bin_dir, stop_file=stop_file)

    writer_out = tmp_path / "writer-batch.out"
    tester_out = tmp_path / "tester-batch.out"

    writer_proc = _start_wake(root, "writer", env, writer_out)
    try:
        # The writer chain holds writer-first's repo lock and is mid-task
        # from this point until tester-first's turn has started.
        _wait_for_marker(sync_dir / "writer-first-started", writer_proc, writer_out)
        tester_proc = _start_wake(root, "tester", env, tester_out)
        try:
            tester_rc, tester_log = _finish_wake(tester_proc, tester_out)
        finally:
            if tester_proc.poll() is None:
                tester_proc.kill()
    finally:
        writer_rc, writer_log = _finish_wake(writer_proc, writer_out)

    context = (
        f"\n--- stub warnings ---\n{_stub_warnings(control_dir)}"
        f"\n--- writer batch ---\n{writer_log}\n--- tester batch ---\n{tester_log}"
    )

    assert _stub_warnings(control_dir) == "", f"the stub handshake did not complete as designed.{context}"
    assert writer_rc == 0, f"writer batch exited {writer_rc}, expected 0.{context}"
    assert tester_rc == 0, f"tester batch exited {tester_rc}, expected 0.{context}"

    # The file that stopped both batches is still exactly where the
    # operator put it: no chain unlinked it.
    assert stop_file.exists(), f"the stop file at {stop_file} was removed by a wake batch.{context}"

    # Exactly the two in-flight tasks ran; nothing dispatched after the
    # file appeared, in either agent's batch.
    assert sorted(_dispatched_projects(control_dir)) == ["tester-first", "writer-first"], (
        f"expected exactly one writer dispatch (writer-first) and one tester dispatch "
        f"(tester-first); dispatch ledger: {_dispatched_projects(control_dir)}.{context}"
    )
    assert _task_state(root, "writer-first", "writer") == "DONE", context
    assert _task_state(root, "tester-first", "tester") == "DONE", (
        f"the tester's in-flight task must run to completion, never be interrupted.{context}"
    )
    assert _task_state(root, "writer-second", "writer") == "BACKLOG", context
    assert _task_state(root, "tester-second", "tester") == "BACKLOG", context

    # Writer batch: its own project stopped with a reason naming the file,
    # then the between-projects boundary stopped the batch before the next
    # project was even entered.
    stop_reason = f'reason="stop file present at {stop_file}'
    assert f"project writer-first: done:" in writer_log and stop_reason in writer_log, (
        f"expected writer-first's chain to end with a stop reason naming {stop_file}.{context}"
    )
    assert f"stop file present at {stop_file} -- stopping the batch before project tester-first" in writer_log, (
        f"expected the writer batch to stop at the between-projects boundary.{context}"
    )
    for later in ("tester-first", "writer-second", "tester-second"):
        assert f"project {later}: beginning chain" not in writer_log, (
            f"writer batch entered project {later} after the stop file appeared.{context}"
        )

    # Tester batch: contended on writer-first (the writer held it), worked
    # tester-first to completion, then stopped at every later boundary --
    # including the retry pass it would otherwise have run for writer-first.
    assert "project writer-first: repo lock held by holder=writer" in tester_log, (
        f"expected the tester batch to be lock-contended on writer-first by the writer.{context}"
    )
    assert "project tester-first: done:" in tester_log and stop_reason in tester_log, (
        f"expected tester-first's chain to end with a stop reason naming {stop_file}.{context}"
    )
    assert f"stop file present at {stop_file} -- stopping the batch before project writer-second" in tester_log, (
        f"expected the tester batch to stop at the between-projects boundary.{context}"
    )
    assert (
        f"retry pass: stop file present at {stop_file} -- performing no retry for 1 contended project(s): writer-first"
        in tester_log
    ), f"expected the contention retry pass to be skipped because of the stop file.{context}"
    assert "retry pass: retrying after end-of-iteration deferral" not in tester_log, (
        f"the retry pass must not run once the stop file exists.{context}"
    )
    for later in ("writer-second", "tester-second"):
        assert f"project {later}: beginning chain" not in tester_log, (
            f"tester batch entered project {later} after the stop file appeared.{context}"
        )


# ---------------------------------------------------------------------------
# Test 2: already present at batch start; operator removal resumes dispatch
# ---------------------------------------------------------------------------


def test_batch_started_while_stop_file_exists_dispatches_nothing_until_operator_removes_it(
    tmp_path: pathlib.Path,
) -> None:
    root = _build_kanban_root(tmp_path)
    stop_file = tmp_path / "wakeup" / "stop"
    bin_dir, control_dir, _sync_dir = _install_stub_provider(tmp_path, stop_file=stop_file, mode="plain")
    env = _wake_env(tmp_path, root, stub_bin_dir=bin_dir, stop_file=stop_file)

    stop_file.parent.mkdir(parents=True, exist_ok=True)
    stop_file.write_text("", encoding="utf-8")

    rc, log = _run_wake(root, "writer", env)
    assert rc == 0, f"writer batch exited {rc}, expected 0.\n{log}"
    assert f"stop file present at {stop_file} -- stopping the batch before project writer-first" in log, (
        f"expected the batch to stop before its first project.\n{log}"
    )
    assert "beginning chain" not in log, f"no project chain may start while the stop file exists.\n{log}"
    assert _dispatched_projects(control_dir) == [], (
        f"nothing may dispatch while the stop file exists; ledger: {_dispatched_projects(control_dir)}\n{log}"
    )
    assert _task_state(root, "writer-first", "writer") == "BACKLOG", log
    assert stop_file.exists(), f"the wake batch removed the operator's stop file at {stop_file}.\n{log}"

    # The operator removes it; the next batch dispatches normally.
    stop_file.unlink()
    rc, log = _run_wake(root, "writer", env)
    assert rc == 0, f"writer batch exited {rc}, expected 0.\n{log}"
    assert _dispatched_projects(control_dir) == ["writer-first", "writer-second"], (
        f"expected both writer tasks to dispatch once the stop file was gone; "
        f"ledger: {_dispatched_projects(control_dir)}\n{log}"
    )
    assert _task_state(root, "writer-first", "writer") == "DONE", log
    assert _task_state(root, "writer-second", "writer") == "DONE", log
    assert "stop file present" not in log, f"no stop-file narration expected once the file is gone.\n{log}"
