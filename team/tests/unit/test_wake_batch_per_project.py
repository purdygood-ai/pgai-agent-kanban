"""
test_wake_batch_per_project.py
==============================
One wake-batch.sh firing starts a batch for every registered project, and
the batches run side by side, so one project's queue never keeps another
project's agent waiting:

  1. Two projects, each with one BACKLOG coder task: one firing logs a
     `beginning chain` line for each, finishes both tasks, and project 2's
     task is dispatched while project 1's dispatch is still running.
  2. Project 1 holds four BACKLOG coder tasks; project 2 holds one WAITING
     coder task whose only prerequisite is DONE. The first firing promotes
     project 2's task and dispatches it while project 1's first dispatch is
     still running.

  3. A projects.cfg that exists but cannot be read (mode 000; skipped as
     root) is refused: exit 1, the dual-channel block names the registry,
     no provider wake script starts, and one open operator-inbox entry
     describes it -- one entry still after a second firing.
  4. A registered name that is not a plain project name gets a log line
     and one open operator-inbox entry naming it, while the other
     registered projects are still woken.
  5. A readable registry with no enabled project still exits 0 with the
     "nothing to wake" line and writes no inbox entry.
  6. A [project:...] section whose name the registry does not accept at
     all ([project:bad.name], [project:bad name]) is not lost: over two
     firings each such name is logged, refused dual-channel and left as
     exactly one open operator-inbox entry, while the valid project is
     woken on both.

Tests 3-6 fire against a lighter install -- wake-batch.sh, scripts/lib/,
pgai_agent_kanban/ and config/ copied beside a stub wake/claude.sh that only
records the project it was started for -- since they are about which
batches start, not what a batch does.

Concurrency is shown by task state and an event file, never by timing: the
stub `claude` dispatched for project 1's first task waits (bounded) until
the event file records a project 2 dispatch, and writes whether it saw one.
Run one project after the other and that dispatch gives up and records
"no".

Each test runs one real firing (`bash scripts/wake-batch.sh --agent=coder`)
against a throwaway kanban root under tmp_path: a copy of this tree's
scripts/, pgai_agent_kanban/, workflows/, pm-agent/ and config/, two
document-workflow projects (no dev tree, worktree or repository), and a
PATH-shadowing `claude` stub. The environment is built from an explicit
set with every live-install name pointed into the sandbox
(docs/test-authoring-guidelines.md "Live-install safety").
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.overwatch import inbox
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_MIRRORED_DIRS = ("scripts", "pgai_agent_kanban", "workflows", "pm-agent", "config")
_PROJECTS = ("alpha", "beta")
_AGENTS = ("pm", "coder", "writer", "tester", "cm", "overwatch")
_WAKE_TIMEOUT_S = 240
# How long project 1's first dispatch waits for a project 2 dispatch before
# recording that it saw none (tenths of a second).
_AWAIT_TENTHS = 300

# The stub `claude`: answers --version; on a dispatch, records
# "dispatch <project> <task>" in the event file, and -- for the first
# dispatch of the hold project only -- waits for a dispatch of the await
# project, recording what it saw. Then marks the task DONE, as a real
# agent turn would, and prints a valid --output-format json payload.
_STUB_CLAUDE = r"""#!/usr/bin/env bash
set -u
EVENTS=__EVENTS__
HOLD_PROJECT=__HOLD__
AWAIT_PROJECT=__AWAIT__
HELD_ONCE=__HELD_ONCE__
for _a in "$@"; do
  if [[ "$_a" == "--version" ]]; then echo "2.1.251 (Claude Code)"; exit 0; fi
done
prompt=""; _prev=""
for _a in "$@"; do [[ "$_prev" == "-p" ]] && prompt="$_a"; _prev="$_a"; done
status_path="$(printf '%s\n' "$prompt" | grep -oE 'Read the status at [^[:space:]]+' | head -n1 | awk '{print $NF}')"
project="$(printf '%s\n' "$status_path" | sed -nE 's#.*/projects/([^/]+)/tasks/.*#\1#p')"
task="$(basename "$(dirname "$status_path")")"
echo "dispatch ${project} ${task}" >> "$EVENTS"
if [[ "$project" == "$HOLD_PROJECT" && ! -e "$HELD_ONCE" ]]; then
  : > "$HELD_ONCE"
  saw=no
  for _ in $(seq 1 __AWAIT_TENTHS__); do
    if grep -q "^dispatch ${AWAIT_PROJECT} " "$EVENTS"; then saw=yes; break; fi
    sleep 0.1
  done
  echo "await ${project} ${task} saw-${AWAIT_PROJECT}=${saw}" >> "$EVENTS"
fi
if [[ -n "$status_path" && -f "$status_path" ]]; then
  printf '# Status\n\n## Task\n%s\n\n## State\nDONE\n\n## Summary\nstub\n\n## Artifacts\nnone\n\n## Blockers\nnone\n\n## Needs Human\nno\n' "$task" > "$status_path"
fi
echo '{"type":"result","result":"stub agent completed the task","usage":{"input_tokens":10,"output_tokens":5,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}'
exit 0
"""


class _Sandbox:
    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.tmp_path = tmp_path
        self.root = tmp_path / "kanban"
        self.bin_dir = tmp_path / "stub-bin"
        self.events = tmp_path / "events.log"
        self._build()

    def _build(self) -> None:
        scratch = self.tmp_path / "scratch"
        scratch.mkdir(parents=True)
        (self.root / "logs").mkdir(parents=True)
        (self.root / "locks").mkdir(parents=True)
        (self.root / "kanban.cfg").write_text(
            "[paths]\n"
            f"tmp_root = {scratch}\n"
            "tmp_subdir = pgai_kanban_tmp\n\n"
            "[chain]\npm_mode = manual\n\n"
            "[wake]\nmax_tasks_per_wake = 8\n",
            encoding="utf-8",
        )
        (self.root / "projects.cfg").write_text(
            "\n".join(f"[project:{name}]\npriority={i}\nenabled=true\n" for i, name in enumerate(_PROJECTS, 1)),
            encoding="utf-8",
        )
        for name in _PROJECTS:
            project = self.root / "projects" / name
            (project / "tasks" / "queues").mkdir(parents=True)
            (project / "requirements").mkdir(parents=True)
            (project / "project.cfg").write_text(
                f"[project]\nproject_name = {name}\nworkflow_type = document\n", encoding="utf-8"
            )
            for agent in _AGENTS:
                (project / "tasks" / "queues" / f"{agent}_backlog.md").write_text(
                    f"# {agent} backlog\n\n", encoding="utf-8"
                )
        for name in _MIRRORED_DIRS:
            shutil.copytree(_TEAM_DIR / name, self.root / name, ignore=shutil.ignore_patterns("__pycache__"))
        self.bin_dir.mkdir()
        stub = self.bin_dir / "claude"
        stub.write_text(
            _STUB_CLAUDE.replace("__EVENTS__", str(self.events))
            .replace("__HOLD__", _PROJECTS[0])
            .replace("__AWAIT__", _PROJECTS[1])
            .replace("__HELD_ONCE__", str(self.tmp_path / "held-once"))
            .replace("__AWAIT_TENTHS__", str(_AWAIT_TENTHS)),
            encoding="utf-8",
        )
        stub.chmod(0o755)

    def add_task(self, project: str, task_id: str, *, state: str, prerequisites: str = "none") -> None:
        tasks = self.root / "projects" / project / "tasks"
        task_dir = tasks / task_id
        task_dir.mkdir(parents=True)
        (task_dir / "README.md").write_text(
            f"# Task: {task_id}\n\n## Task ID\n{task_id}\n\n## Role\nCODER\n\n"
            "## Workflow Type\ndocument\n\n## Git Repo\nnone\n\n## Working Directory\nnone\n\n"
            f"## Source Branch\nnone\n\n## Prerequisites\n{prerequisites}\n",
            encoding="utf-8",
        )
        (task_dir / "status.md").write_text(
            f"# Status\n\n## Task\n{task_id}\n\n## State\n{state}\n\n## Summary\nnone\n\n"
            "## Blockers\nnone\n\n## Needs Human\nno\n",
            encoding="utf-8",
        )
        marker = {"BLOCKED": "B", "WAITING": "W", "DONE": "x"}.get(state, " ")
        with (tasks / "queues" / "coder_backlog.md").open("a", encoding="utf-8") as handle:
            handle.write(f"- [{marker}] {task_id} — fixture task\n")

    def state(self, project: str, task_id: str) -> str:
        text = (self.root / "projects" / project / "tasks" / task_id / "status.md").read_text(encoding="utf-8")
        return text.split("## State", 1)[1].strip().splitlines()[0].strip()

    def fire(self, agent: str = "coder") -> subprocess.CompletedProcess:
        (self.tmp_path / "home").mkdir(exist_ok=True)
        env = build_scrubbed_env(
            home_dir=self.tmp_path / "home",
            extra_path_dirs=[self.bin_dir, pathlib.Path(sys.executable).parent],
            extra_env={
                "KANBAN_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.tmp_path / "scratch"),
                "PAUSE_BETWEEN_TASKS": "0",
            },
        )
        cwd = self.tmp_path / "foreign_cwd"
        cwd.mkdir(exist_ok=True)
        return subprocess.run(
            ["bash", str(self.root / "scripts" / "wake-batch.sh"), f"--agent={agent}"],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=_WAKE_TIMEOUT_S,
        )

    def event_lines(self) -> list[str]:
        return self.events.read_text(encoding="utf-8").splitlines() if self.events.exists() else []


def _output(result: subprocess.CompletedProcess) -> str:
    return result.stdout + result.stderr


def test_one_firing_runs_both_projects_side_by_side(tmp_path) -> None:
    box = _Sandbox(tmp_path)
    first = "CODER-20260101-001-alpha-work"
    second = "CODER-20260101-002-beta-work"
    box.add_task("alpha", first, state="BACKLOG")
    box.add_task("beta", second, state="BACKLOG")

    result = box.fire("coder")
    out = _output(result)

    assert result.returncode == 0, out
    assert "project alpha: beginning chain" in out, out
    assert "project beta: beginning chain" in out, out
    assert "batch scope: project alpha only" in out and "batch scope: project beta only" in out, out
    assert box.state("alpha", first) == "DONE", out
    assert box.state("beta", second) == "DONE", out
    events = box.event_lines()
    assert f"await alpha {first} saw-beta=yes" in events, f"beta was not dispatched while alpha's ran: {events}"
    for project in _PROJECTS:
        assert (box.root / "locks" / f"wake-batch-coder-{project}.record").is_file()
    batch_logs = sorted(p.name for p in (box.root / "logs" / "agents").glob("coder-batch-*.log"))
    assert any(n.endswith("-alpha.log") for n in batch_logs) and any(
        n.endswith("-beta.log") for n in batch_logs
    ), batch_logs


def test_a_waiting_task_in_project_2_runs_while_project_1_has_a_long_queue(tmp_path) -> None:
    box = _Sandbox(tmp_path)
    alpha_tasks = [f"CODER-20260101-00{n}-alpha-queue-{n}" for n in range(1, 5)]
    for task in alpha_tasks:
        box.add_task("alpha", task, state="BACKLOG")
    prereq = "CODER-20260101-010-beta-finished-prerequisite"
    waiting = "CODER-20260101-011-beta-waiting-on-finished"
    box.add_task("beta", prereq, state="DONE")
    box.add_task("beta", waiting, state="WAITING", prerequisites=prereq)

    result = box.fire("coder")
    out = _output(result)

    assert result.returncode == 0, out
    assert box.state("beta", waiting) == "DONE", out
    events = box.event_lines()
    assert f"dispatch beta {waiting}" in events, events
    held = [e for e in events if e.startswith("await alpha ")]
    assert held == [
        f"await alpha {alpha_tasks[0]} saw-beta=yes"
    ], f"beta's promoted task was not dispatched while alpha's first task ran: {events}"
    assert all(box.state("alpha", task) == "DONE" for task in alpha_tasks), out


# ---------------------------------------------------------------------------
# Registry faults: an unreadable projects.cfg, a name that is not plain
# ---------------------------------------------------------------------------

# The stub provider wake: records the project its batch was started for.
_STUB_WAKE = """#!/usr/bin/env bash
echo "${PGAI_WAKE_BATCH_PROJECT:-}" >> "$PGAI_AGENT_KANBAN_ROOT_PATH/stub-ran"
exit 0
"""


class _StubInstall:
    """wake-batch.sh, scripts/lib/, pgai_agent_kanban/ and config/ with a stub provider, over a sandbox root."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.tmp_path = tmp_path
        install = tmp_path / "install"
        scripts = install / "scripts"
        (scripts / "wake").mkdir(parents=True)
        shutil.copy(_TEAM_DIR / "scripts" / "wake-batch.sh", scripts / "wake-batch.sh")
        shutil.copytree(_TEAM_DIR / "scripts" / "lib", scripts / "lib", ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("pgai_agent_kanban", "config"):
            shutil.copytree(_TEAM_DIR / name, install / name, ignore=shutil.ignore_patterns("__pycache__"))
        stub = scripts / "wake" / "claude.sh"
        stub.write_text(_STUB_WAKE, encoding="utf-8")
        stub.chmod(0o755)
        self.script = scripts / "wake-batch.sh"
        self.root = tmp_path / "sandbox"
        (self.root / "tmp").mkdir(parents=True)
        self.registry = self.root / "projects.cfg"

    def register(self, *names: str, disabled: tuple[str, ...] = ()) -> None:
        sections = []
        for priority, name in enumerate(names, start=1):
            if name.replace("-", "").isalnum() and name == name.lower():
                (self.root / "projects" / name).mkdir(parents=True, exist_ok=True)
            enabled = "false" if name in disabled else "true"
            sections.append(f"[project:{name}]\npriority={priority}\nenabled={enabled}\n")
        self.registry.write_text("\n".join(sections), encoding="utf-8")

    def fire(self) -> subprocess.CompletedProcess:
        env = build_scrubbed_env(
            home_dir=self.tmp_path / "home",
            extra_path_dirs=[pathlib.Path(sys.executable).parent],
            extra_env={
                "KANBAN_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
            },
        )
        for name in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "PGAI_PROJECT_NAME"):
            env.pop(name, None)
        return subprocess.run(
            ["bash", str(self.script), "--agent=coder"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(self.tmp_path),
            timeout=120,
            check=False,
        )

    def ran(self) -> list[str]:
        path = self.root / "stub-ran"
        return sorted(path.read_text(encoding="utf-8").split()) if path.exists() else []

    def open_entries(self, project: str) -> tuple:
        return inbox.list_open(OpsContext(kanban_root=self.root), project)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_an_unreadable_registry_is_refused_and_parked_in_the_inbox(tmp_path) -> None:
    box = _StubInstall(tmp_path)
    box.register("alpha", "beta")
    box.registry.chmod(0o000)
    try:
        first = box.fire()
        second = box.fire()
    finally:
        box.registry.chmod(0o644)

    out = _output(first)
    assert first.returncode == 1, out
    assert "error:   wake_batch.registry_unreadable" in first.stderr, out
    assert str(box.registry) in first.stderr.split("message:", 1)[1], out
    assert f"remedy:  chmod u+r {box.registry}" in first.stderr, out
    assert "nothing to wake" not in out
    assert box.ran() == [], "a provider wake script started despite the unreadable registry"
    assert second.returncode == 1, _output(second)
    (entry,) = box.open_entries("alpha")
    assert entry.source == "wake-batch"
    assert str(box.registry) in entry.question and "cannot be read" in entry.question
    assert f"operator inbox entry {entry.entry_id} in project alpha" in out
    assert box.open_entries("beta") == ()


def test_a_name_that_is_not_plain_is_named_while_the_others_are_woken(tmp_path) -> None:
    box = _StubInstall(tmp_path)
    box.register("alpha", "_bad", "beta")

    result = box.fire()
    out = _output(result)

    assert result.returncode == 0, out
    assert box.ran() == ["alpha", "beta"], out
    logged = [line for line in result.stdout.splitlines() if "'_bad'" in line]
    assert len(logged) == 1 and "not a plain project name" in logged[0], result.stdout
    assert "error:   wake_batch.project_name_not_plain" in result.stderr, out
    (entry,) = box.open_entries("alpha")
    assert "'_bad'" in entry.question and entry.source == "wake-batch"
    assert box.open_entries("beta") == ()


def test_a_readable_registry_with_no_enabled_project_still_has_nothing_to_wake(tmp_path) -> None:
    box = _StubInstall(tmp_path)
    box.register("alpha", disabled=("alpha",))

    result = box.fire()

    assert result.returncode == 0, _output(result)
    assert "wake-batch(coder): no registered, enabled project in " in result.stdout
    assert box.ran() == []
    assert not (box.root / "projects" / "alpha" / "operator-inbox.md").exists()


def test_a_section_name_the_registry_rejects_is_refused_by_name_once(tmp_path) -> None:
    box = _StubInstall(tmp_path)
    bad_names = ("bad.name", "bad name")
    box.register("alpha", *bad_names)

    first = box.fire()
    second = box.fire()

    for result in (first, second):
        out = _output(result)
        assert result.returncode == 0, out
        stderr_blocks = result.stderr.split("error:   ")
        for name in bad_names:
            logged = [
                line
                for line in result.stdout.splitlines()
                if f"'{name}'" in line and "not a plain project name" in line
            ]
            assert len(logged) == 1, out
            refusals = [
                block
                for block in stderr_blocks
                if block.startswith("wake_batch.project_name_not_plain") and f"'{name}'" in block
            ]
            assert len(refusals) == 1, out
            (refusal,) = refusals
            assert "message:" in refusal and "remedy:" in refusal, out
            assert "grep -n -F --" in refusal.split("remedy:", 1)[1], out
    path = box.root / "stub-ran"
    assert path.read_text(encoding="utf-8").splitlines() == ["alpha", "alpha"], _output(first) + _output(second)
    entries = box.open_entries("alpha")
    for name in bad_names:
        named = [entry for entry in entries if f"'{name}'" in entry.question]
        assert len(named) == 1, [entry.question for entry in entries]
        assert named[0].source == "wake-batch"
    assert len(entries) == len(bad_names), [entry.question for entry in entries]
