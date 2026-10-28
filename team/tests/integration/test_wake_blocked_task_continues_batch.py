"""
test_wake_blocked_task_continues_batch.py
=========================================
End-to-end coverage for what a task that ends BLOCKED stops, under
``[wake] stop_on_blocked``:

  * default (``false``) -- the BLOCKED task parks only itself. Its dependant
    (a task whose ``## Prerequisites`` names it) goes WAITING and is never
    dispatched; an independent BACKLOG task in the same project is
    dispatched in the same batch; the next registered project's batch runs
    its own task unaffected.
  * ``stop_on_blocked = true`` -- the project's batch stops right after the
    BLOCKED task with the ``STOP_ON_BLOCKED=true`` stop reason, and the
    independent task is not dispatched.

Every test drives the REAL production entry point (``bash
scripts/wake/claude.sh --agent=writer``) as a subprocess against a
throwaway kanban root with two registered document-workflow projects
(no dev tree or git repository needed). A PATH-shadowing ``claude`` stub
completes each dispatch: the task whose ID carries ``-blocks-`` is written
BLOCKED with a Block Record, as an agent's own block writes it; every other
task is written DONE. The stub never reaches a real model.

Fixture batch
-------------
  first-project   A  (_TASK_A)  -> the stub BLOCKS it
                  B  (_TASK_B)  -> no prerequisites
                  C  (_TASK_C)  -> ## Prerequisites: A
  second-project  D  (_TASK_D)  -> no prerequisites

Queue order in first-project is A, B, C, so B is the task directly behind
the block.

Design notes
------------
- The kanban root is a self-contained copy of this tree's scripts/,
  pgai_agent_kanban/, workflows/, pm-agent/ and config/ (the set install.sh
  mirrors into a live install), so the wake under test is byte-identical
  to production and self-locates there.
- The subprocess environment is built from an explicit minimal set of
  names (tests.fixtures.scrubbed_subprocess_env.build_scrubbed_env), so a
  host's own STOP_ON_BLOCKED or other wake tunables cannot leak in; the
  setting under test comes from the sandbox kanban.cfg alone.
- A chain that blocks a task nudges OVERWATCH's block triage in the
  background (fire_block_triage_nudge). Each test waits for that sweep to
  report completion so no process outlives the test's tmp_path.
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

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]  # team/
_WAKE_CLAUDE_REL = pathlib.Path("scripts") / "wake" / "claude.sh"
_MIRRORED_DIRS = ("scripts", "pgai_agent_kanban", "workflows", "pm-agent", "config")
_ALL_AGENTS = ("pm", "coder", "writer", "tester", "cm", "overwatch")

_FIRST = "first-project"
_SECOND = "second-project"
_TASK_A = "WRITER-20260101-001-first-blocks"
_TASK_B = "WRITER-20260101-002-first-independent"
_TASK_C = "WRITER-20260101-003-first-dependant"
_TASK_D = "WRITER-20260101-004-second-independent"

# project -> [(task id, prerequisite task ids)], in queue order.
_QUEUES = {
    _FIRST: [(_TASK_A, ()), (_TASK_B, ()), (_TASK_C, (_TASK_A,))],
    _SECOND: [(_TASK_D, ())],
}

_STOP_ON_BLOCKED_REASON = 'reason="task ended in BLOCKED state (STOP_ON_BLOCKED=true)"'
_BLOCKED_STOP_TEXT = "task ended in BLOCKED state"
_WAKE_TIMEOUT_SECONDS = 240
_SWEEP_WAIT_SECONDS = 180
_SWEEP_DONE = "overwatch-sweep: complete"


# ---------------------------------------------------------------------------
# Fixture-tree builders
# ---------------------------------------------------------------------------


def _write_task(tasks_dir: pathlib.Path, task_id: str, prerequisites: tuple[str, ...]) -> None:
    """A minimal document-workflow writer task queued BACKLOG."""
    task_dir = tasks_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    prereq_block = ""
    if prerequisites:
        prereq_block = "## Prerequisites\n" + "".join(f"- {p}\n" for p in prerequisites) + "\n"
    (task_dir / "README.md").write_text(
        f"# Task: {task_id}\n\n"
        f"## Task ID\n{task_id}\n\n"
        "## Role\nWRITER\n\n"
        "## Workflow Type\ndocument\n\n"
        "## Git Repo\nnone\n\n"
        "## Working Directory\nnone\n\n"
        "## Source Branch\nnone\n\n"
        f"{prereq_block}",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{task_id}\n\n## State\nBACKLOG\n\n## Summary\nnone\n\n"
        "## Blockers\nnone\n\n## Needs Human\nno\n",
        encoding="utf-8",
    )


def _build_kanban_root(tmp_path: pathlib.Path, *, stop_on_blocked: str | None) -> pathlib.Path:
    """Register the two fixture projects with the _QUEUES writer tasks.

    *stop_on_blocked* None leaves the key out of kanban.cfg (the shipped
    default applies); a string writes it under [wake] verbatim.
    """
    root = tmp_path / "kanban_root"
    root.mkdir(parents=True)
    scratch = tmp_path / "kanban_scratch"
    scratch.mkdir(parents=True)

    wake_section = "[wake]\nmax_tasks_per_wake = 5\n"
    if stop_on_blocked is not None:
        wake_section += f"stop_on_blocked = {stop_on_blocked}\n"
    (root / "kanban.cfg").write_text(
        f"[paths]\ntmp_root = {scratch}\ntmp_subdir = pgai_kanban_tmp\n\n"
        "[chain]\npm_mode = manual\n\n" + wake_section,
        encoding="utf-8",
    )
    (root / "projects.cfg").write_text(
        "".join(
            f"[project:{name}]\npriority={i}\nenabled=true\n\n"
            for i, name in enumerate(_QUEUES, start=1)
        ),
        encoding="utf-8",
    )

    for name, tasks in _QUEUES.items():
        proj = root / "projects" / name
        queues_dir = proj / "tasks" / "queues"
        queues_dir.mkdir(parents=True)
        (proj / "overwatch").mkdir()
        (proj / "project.cfg").write_text(
            f"[project]\nproject_name = {name}\nworkflow_type = document\n",
            encoding="utf-8",
        )
        for task_id, prereqs in tasks:
            _write_task(proj / "tasks", task_id, prereqs)
        for agent in _ALL_AGENTS:
            body = f"# {name} {agent} backlog\n\n"
            if agent == "writer":
                body += "".join(f"- [ ] {task_id} — fixture task\n" for task_id, _ in tasks)
            (queues_dir / f"{agent}_backlog.md").write_text(body, encoding="utf-8")

    (root / "logs").mkdir()
    (root / "locks").mkdir()
    for dirname in _MIRRORED_DIRS:
        shutil.copytree(_TEAM_DIR / dirname, root / dirname, ignore=shutil.ignore_patterns("__pycache__"))
    return root


def _install_stub_provider(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """Write the PATH-shadowing `claude` stub. Returns (bin_dir, ledger).

    Every dispatch appends "<project> <task-id>" to the ledger, the record
    the tests read to prove which tasks dispatched and which never did.
    """
    bin_dir = tmp_path / "stub_provider_bin"
    bin_dir.mkdir()
    ledger = tmp_path / "dispatches.log"
    script = textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        # Test-only stand-in for the provider CLI: blocks the task whose ID
        # carries -blocks-, completes every other one. Never reaches a model.
        set -u
        for _arg in "$@"; do
          [[ "$_arg" == "--version" ]] && {{ echo "2.1.251 (Claude Code)"; exit 0; }}
        done
        task="${{PGAI_TASK_ID:-<unset>}}"
        echo "${{PGAI_PROJECT_NAME:-<unset>}} $task" >> {str(ledger)!r}
        prompt="" _prev=""
        for _a in "$@"; do [[ "$_prev" == "-p" ]] && prompt="$_a"; _prev="$_a"; done
        status="$(printf '%s\\n' "$prompt" | grep -oE 'Read the status at [^[:space:]]+' | head -n1 | awk '{{print $NF}}')"
        if [[ -n "$status" && -f "$status" ]]; then
          if [[ "$task" == *-blocks* ]]; then
            cat > "$status" <<'EOF'
        # Status

        ## State
        BLOCKED

        ## Summary
        Stub agent blocked the task.

        ## Blockers
        stub agent could not continue

        ## Needs Human
        yes

        ## Block Record
        reason_code: agent_unclassified
        owner_role: WRITER
        attempts: 0
        first_blocked_at: 2026-01-01T10:00:00+00:00
        last_blocked_at: 2026-01-01T10:00:00+00:00
        cooldown_until: none
        evidence: none
        resume: fresh
        EOF
          else
            printf '# Status\\n\\n## State\\nDONE\\n\\n## Summary\\nStub agent finished.\\n\\n## Blockers\\nnone\\n\\n## Needs Human\\nno\\n' > "$status"
          fi
        fi
        echo '{{"type":"result","result":"stub","usage":{{"input_tokens":1,"output_tokens":1,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}}}'
        exit 0
        """
    )
    stub = bin_dir / "claude"
    stub.write_text(script, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    python3_link = bin_dir / "python3"
    python3_link.symlink_to(pathlib.Path(sys.executable).resolve())
    return bin_dir, ledger


def _run_writer_wake(tmp_path: pathlib.Path, root: pathlib.Path, bin_dir: pathlib.Path) -> tuple[int, str]:
    home = tmp_path / "home"
    home.mkdir()
    env = build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=(bin_dir,),
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "kanban_scratch" / "pgai_kanban_tmp"),
            "PAUSE_BETWEEN_TASKS": "0",
        },
    )
    cwd = tmp_path / "foreign_cwd"
    cwd.mkdir()
    completed = subprocess.run(
        ["bash", str(root / _WAKE_CLAUDE_REL), "--agent=writer"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=_WAKE_TIMEOUT_SECONDS,
    )
    return completed.returncode, completed.stdout + completed.stderr


def _wait_for_block_triage_sweep(root: pathlib.Path) -> None:
    """Wait for the background sweep the on-BLOCK nudge started to finish,
    so it never outlives the test's tmp_path."""
    trigger_log = root / "logs" / "overwatch-trigger.log"
    deadline = time.time() + _SWEEP_WAIT_SECONDS
    while time.time() < deadline:
        if trigger_log.is_file() and _SWEEP_DONE in trigger_log.read_text(encoding="utf-8"):
            return
        time.sleep(0.5)
    text = trigger_log.read_text(encoding="utf-8") if trigger_log.is_file() else "(no trigger log)"
    raise AssertionError(f"the nudged block-triage sweep did not complete within {_SWEEP_WAIT_SECONDS}s:\n{text}")


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


def _state(root: pathlib.Path, project: str, task_id: str) -> str:
    status = root / "projects" / project / "tasks" / task_id / "status.md"
    m = re.search(r"^##\s+State\s*\n+(.*?)\s*(?:\n+##|\Z)", status.read_text(encoding="utf-8"), flags=re.S | re.M)
    return m.group(1).strip() if m else f"<no ## State in {status}>"


def _dispatched(ledger: pathlib.Path) -> list[str]:
    if not ledger.exists():
        return []
    return [line.split()[1] for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]


def _project_lines(log: str, project: str) -> list[str]:
    """The batch log lines naming *project*."""
    return [line for line in log.splitlines() if f"project {project}:" in line]


# ---------------------------------------------------------------------------
# Default: a BLOCKED task parks only itself
# ---------------------------------------------------------------------------


def test_default_blocked_task_parks_itself_and_batch_continues(tmp_path: pathlib.Path) -> None:
    root = _build_kanban_root(tmp_path, stop_on_blocked=None)
    bin_dir, ledger = _install_stub_provider(tmp_path)

    rc, log = _run_writer_wake(tmp_path, root, bin_dir)
    _wait_for_block_triage_sweep(root)
    context = f"\n--- dispatch ledger ---\n{_dispatched(ledger)}\n--- batch log ---\n{log}"

    assert rc == 0, f"writer batch exited {rc}, expected 0.{context}"
    assert "stop_on_blocked=false" in log, f"expected the shipped default stop_on_blocked=false.{context}"

    # A blocked, and B -- the independent task right behind it -- dispatched
    # in the same batch; C, which names A as a prerequisite, never did.
    assert _state(root, _FIRST, _TASK_A) == "BLOCKED", context
    assert _state(root, _FIRST, _TASK_B) == "DONE", (
        f"the independent task behind the block must be dispatched in the same batch.{context}"
    )
    assert _state(root, _FIRST, _TASK_C) == "WAITING", (
        f"the task whose prerequisite is the BLOCKED task must stay WAITING.{context}"
    )
    assert _TASK_C not in _dispatched(ledger), f"the dependant task was dispatched.{context}"

    # The second project's batch ran its own task unaffected.
    assert _state(root, _SECOND, _TASK_D) == "DONE", context
    assert _dispatched(ledger) == [_TASK_A, _TASK_B, _TASK_D], (
        f"expected exactly A, B, then the second project's task to dispatch.{context}"
    )

    # No BLOCKED stop reason anywhere in the batch log; the first project's
    # chain ended because its queue ran dry, and narrated the continue.
    assert _BLOCKED_STOP_TEXT not in log, f"the batch stopped on the BLOCKED task.{context}"
    first_done = [line for line in _project_lines(log, _FIRST) if ": done:" in line]
    assert first_done and 'reason="no more BACKLOG tasks' in first_done[-1], (
        f"expected the first project's chain to end on an empty queue.{context}"
    )
    assert any("a task ended BLOCKED; stop_on_blocked=false -- continuing" in line
               for line in _project_lines(log, _FIRST)), (
        f"expected the chain to narrate that it continued past the BLOCKED task.{context}"
    )
    assert any("beginning chain" in line for line in _project_lines(log, _SECOND)), context


def test_explicit_false_behaves_as_the_default(tmp_path: pathlib.Path) -> None:
    root = _build_kanban_root(tmp_path, stop_on_blocked="false")
    bin_dir, ledger = _install_stub_provider(tmp_path)

    rc, log = _run_writer_wake(tmp_path, root, bin_dir)
    _wait_for_block_triage_sweep(root)
    context = f"\n--- dispatch ledger ---\n{_dispatched(ledger)}\n--- batch log ---\n{log}"

    assert rc == 0, f"writer batch exited {rc}, expected 0.{context}"
    assert _dispatched(ledger) == [_TASK_A, _TASK_B, _TASK_D], context
    assert _state(root, _FIRST, _TASK_C) == "WAITING", context
    assert _BLOCKED_STOP_TEXT not in log, context


# ---------------------------------------------------------------------------
# stop_on_blocked = true: the old stop-the-batch behaviour
# ---------------------------------------------------------------------------


def test_stop_on_blocked_true_stops_the_project_batch_after_the_blocked_task(tmp_path: pathlib.Path) -> None:
    root = _build_kanban_root(tmp_path, stop_on_blocked="true")
    bin_dir, ledger = _install_stub_provider(tmp_path)

    rc, log = _run_writer_wake(tmp_path, root, bin_dir)
    _wait_for_block_triage_sweep(root)
    context = f"\n--- dispatch ledger ---\n{_dispatched(ledger)}\n--- batch log ---\n{log}"

    assert rc == 0, f"writer batch exited {rc}, expected 0.{context}"
    assert "stop_on_blocked=true" in log, context
    assert _state(root, _FIRST, _TASK_A) == "BLOCKED", context

    # The first project's chain stopped right after A with the existing
    # stop reason; neither B nor C was dispatched.
    first_done = [line for line in _project_lines(log, _FIRST) if ": done:" in line]
    assert first_done and _STOP_ON_BLOCKED_REASON in first_done[-1], (
        f"expected the first project's chain to stop with {_STOP_ON_BLOCKED_REASON}.{context}"
    )
    assert _TASK_B not in _dispatched(ledger), f"the independent task was dispatched after the stop.{context}"
    assert _TASK_C not in _dispatched(ledger), context
    assert _state(root, _FIRST, _TASK_B) == "BACKLOG", context
    assert "continuing the batch with the next independent task" not in log, context
